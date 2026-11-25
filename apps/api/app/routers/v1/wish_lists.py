"""Wish lists: the owner's and editors' endpoints (viewers read published lists)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import PublicLink, User, WishList, WishListClaim, WishListItem
from app.routers.v1.diaries import _get_diary_or_404
from app.services.public_links import create_link, revoke_links
from app.services.read_only import ensure_diary_writable
from app.services.wish_lists import (
    MAX_ITEMS,
    ItemCreate,
    ItemOut,
    ItemPatch,
    WishListCreate,
    WishListOut,
    WishListPatch,
    WishListSummaryOut,
    claim_item,
    coming_up,
    item_out,
    load_item,
    load_item_full,
    load_list,
    undo_claim,
    wish_list_out,
)

router = APIRouter(tags=["wish-lists"])


def _require_manager(role: str | None) -> None:
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")


async def _open_list(
    db: AsyncSession, wish_list_id: uuid.UUID, user: User, *, lock: bool = False
) -> tuple[WishList, str | None]:
    """The list and the caller's role. A viewer asking for a draft gets 404."""
    wl = await load_list(db, wish_list_id, lock=lock)
    diary, role = await _get_diary_or_404(wl.diary_id, user, db)
    if role == "viewer" and wl.status != "published":
        raise HTTPException(status_code=404, detail="not_found")
    return wl, role


async def _manage_list(
    db: AsyncSession,
    wish_list_id: uuid.UUID,
    user: User,
    *,
    writable: bool = True,
    lock: bool = False,
):
    wl, role = await _open_list(db, wish_list_id, user, lock=lock)
    _require_manager(role)
    diary, _ = await _get_diary_or_404(wl.diary_id, user, db)
    if writable:
        await ensure_diary_writable(db, diary)
    return wl, diary, role


@router.post(
    "/diaries/{diary_id}/wish-lists",
    response_model=WishListOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_wish_list(
    diary_id: uuid.UUID,
    body: WishListCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    _require_manager(role)
    await ensure_diary_writable(db, diary)
    wl = WishList(
        diary_id=diary.id,
        title=body.title,
        list_date=body.list_date,
        pasted_list_url=body.pasted_list_url,
        created_by_user_id=user.id,
    )
    db.add(wl)
    await db.flush()
    out = await wish_list_out(db, await load_list(db, wl.id), caller=user, manager=True)
    await db.commit()
    return out


@router.get("/diaries/{diary_id}/wish-lists", response_model=list[WishListSummaryOut])
async def list_wish_lists(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[WishListSummaryOut]:
    _, role = await _get_diary_or_404(diary_id, user, db)
    stmt = (
        select(WishList, func.count(WishListItem.id))
        .outerjoin(WishListItem, WishListItem.wish_list_id == WishList.id)
        .where(WishList.diary_id == diary_id)
        .group_by(WishList.id)
        .order_by(WishList.list_date, WishList.created_at)
    )
    if role == "viewer":
        stmt = stmt.where(WishList.status == "published")
    rows = (await db.execute(stmt)).all()
    return [
        WishListSummaryOut(
            id=wl.id,
            diary_id=wl.diary_id,
            title=wl.title,
            list_date=wl.list_date,
            status=wl.status,
            item_count=n,
        )
        for wl, n in rows
    ]


@router.get("/wish-lists/{wish_list_id}", response_model=WishListOut)
async def get_wish_list(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, role = await _open_list(db, wish_list_id, user)
    return await wish_list_out(db, wl, caller=user, manager=role != "viewer")


@router.patch("/wish-lists/{wish_list_id}", response_model=WishListOut)
async def patch_wish_list(
    wish_list_id: uuid.UUID,
    body: WishListPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, _, _ = await _manage_list(db, wish_list_id, user)
    sent = body.model_fields_set
    if body.title is not None:
        wl.title = body.title
    if body.list_date is not None:
        wl.list_date = body.list_date
    if "pasted_list_url" in sent:
        wl.pasted_list_url = body.pasted_list_url
    await db.flush()
    out = await wish_list_out(db, wl, caller=user, manager=True)
    await db.commit()
    return out


@router.delete("/wish-lists/{wish_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wish_list(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    wl, _, _ = await _manage_list(db, wish_list_id, user, writable=False)
    await revoke_links(db, kind="wish_list", target_id=wl.id)
    # Nothing may point at a dead target, so the rows go, not just the tokens.
    await db.execute(
        delete(PublicLink).where(PublicLink.kind == "wish_list", PublicLink.target_id == wl.id)
    )
    await db.execute(delete(WishList).where(WishList.id == wl.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/wish-lists/{wish_list_id}/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED
)
async def add_item(
    wish_list_id: uuid.UUID,
    body: ItemCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ItemOut:
    # The list row lock makes the cap and the next position safe against a parallel add.
    wl, _, _ = await _manage_list(db, wish_list_id, user, lock=True)
    count, top = (
        await db.execute(
            select(func.count(), func.coalesce(func.max(WishListItem.position), -1)).where(
                WishListItem.wish_list_id == wl.id
            )
        )
    ).one()
    if count >= MAX_ITEMS:
        raise HTTPException(status_code=409, detail="wish_list_full")
    item = WishListItem(
        wish_list_id=wl.id,
        position=top + 1,
        name=body.name,
        url=body.url,
        note=body.note or None,
        many=body.many,
    )
    db.add(item)
    await db.flush()
    item = (
        await db.execute(
            select(WishListItem)
            .where(WishListItem.id == item.id)
            .options(selectinload(WishListItem.claims).selectinload(WishListClaim.user))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    out = item_out(item, caller_id=user.id, manager=True)
    await db.commit()
    return out


async def _manage_item(db: AsyncSession, item_id: uuid.UUID, user: User) -> WishListItem:
    item = await load_item(db, item_id)
    await _manage_list(db, item.wish_list_id, user)
    return item


@router.patch("/wish-list-items/{item_id}", response_model=ItemOut)
async def patch_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ItemOut:
    item = await _manage_item(db, item_id, user)
    sent = body.model_fields_set
    if body.name is not None:
        item.name = body.name
    if "url" in sent:
        item.url = body.url
    if "note" in sent:
        item.note = body.note or None
    if body.many is not None:
        item.many = body.many
    await db.flush()
    item = (
        await db.execute(
            select(WishListItem)
            .where(WishListItem.id == item.id)
            .options(selectinload(WishListItem.claims).selectinload(WishListClaim.user))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    out = item_out(item, caller_id=user.id, manager=True)
    await db.commit()
    return out


@router.delete("/wish-list-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    item = await _manage_item(db, item_id, user)
    await db.execute(delete(WishListItem).where(WishListItem.id == item.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/wish-lists/{wish_list_id}/publish", response_model=WishListOut)
async def publish_wish_list(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, diary, role = await _manage_list(db, wish_list_id, user, lock=True)
    if wl.status != "published":
        owner = (await db.execute(select(User).where(User.id == diary.owner_user_id))).scalar_one()
        if not (owner.display_name or "").strip():
            # The public page names who shared it, so the owner needs a name first.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "display_name_required",
                    "details": {"caller_is_owner": role is None},
                },
            )
        wl.status = "published"
        wl.published_at = datetime.now(tz=UTC)
        await create_link(db, diary_id=wl.diary_id, kind="wish_list", target_id=wl.id)
    # Already published: leave the link alone (it may be in a group chat); link/new
    # is the only way to replace it.
    out = await wish_list_out(db, wl, caller=user, manager=True)
    await db.commit()
    return out


@router.post("/wish-lists/{wish_list_id}/unpublish", response_model=WishListOut)
async def unpublish_wish_list(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, _, _ = await _manage_list(db, wish_list_id, user, writable=False, lock=True)
    wl.status = "draft"
    wl.published_at = None
    await revoke_links(db, kind="wish_list", target_id=wl.id)
    out = await wish_list_out(db, wl, caller=user, manager=True)
    await db.commit()
    return out


@router.post("/wish-lists/{wish_list_id}/link/off", response_model=WishListOut)
async def link_off(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, _, _ = await _manage_list(db, wish_list_id, user, writable=False, lock=True)
    await revoke_links(db, kind="wish_list", target_id=wl.id)
    out = await wish_list_out(db, wl, caller=user, manager=True)
    await db.commit()
    return out


@router.post("/wish-lists/{wish_list_id}/link/new", response_model=WishListOut)
async def link_new(
    wish_list_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> WishListOut:
    wl, _, _ = await _manage_list(db, wish_list_id, user, lock=True)
    if wl.status != "published":
        raise HTTPException(status_code=409, detail="wish_list_not_published")
    await create_link(db, diary_id=wl.diary_id, kind="wish_list", target_id=wl.id)
    out = await wish_list_out(db, wl, caller=user, manager=True)
    await db.commit()
    return out


async def _claimable_item(
    db: AsyncSession, item_id: uuid.UUID, user: User
) -> tuple[WishListItem, str | None]:
    """The item and the caller's role; 404 unless the list is published and visible.

    Claiming is not a write to the diary, so read-only diaries are fine.
    """
    item = await load_item(db, item_id)
    wl, role = await _open_list(db, item.wish_list_id, user)
    if wl.status != "published":
        raise HTTPException(status_code=404, detail="not_found")
    return item, role


@router.post("/wish-list-items/{item_id}/claim", response_model=ItemOut)
async def claim(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ItemOut:
    item, role = await _claimable_item(db, item_id, user)
    await claim_item(db, item.id, user=user)
    out = item_out(await load_item_full(db, item.id), caller_id=user.id, manager=role != "viewer")
    await db.commit()
    return out


@router.delete("/wish-list-items/{item_id}/claim", response_model=ItemOut)
async def unclaim(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ItemOut:
    item, role = await _claimable_item(db, item_id, user)
    mine = (
        await db.execute(
            select(WishListClaim.id).where(
                WishListClaim.item_id == item.id, WishListClaim.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if mine is None:
        raise HTTPException(status_code=404, detail="claim_not_found")
    await undo_claim(db, mine, user=user)
    out = item_out(await load_item_full(db, item.id), caller_id=user.id, manager=role != "viewer")
    await db.commit()
    return out


@router.delete("/wish-list-claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def release_claim(
    claim_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    item_id = (
        await db.execute(select(WishListClaim.item_id).where(WishListClaim.id == claim_id))
    ).scalar_one_or_none()
    if item_id is None:
        raise HTTPException(status_code=404, detail="claim_not_found")
    # Releasing is allowed on read-only diaries, like unpublishing.
    item = await load_item(db, item_id)
    await _manage_list(db, item.wish_list_id, user, writable=False)
    await db.execute(delete(WishListClaim).where(WishListClaim.id == claim_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/diaries/{diary_id}/coming-up")
async def get_coming_up(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, list[WishListSummaryOut]]:
    diary, _ = await _get_diary_or_404(diary_id, user, db)
    lists = await coming_up(db, diary)
    counts = dict(
        (
            await db.execute(
                select(WishListItem.wish_list_id, func.count())
                .where(WishListItem.wish_list_id.in_([w.id for w in lists]))
                .group_by(WishListItem.wish_list_id)
            )
        ).all()
    )
    return {
        "wish_lists": [
            WishListSummaryOut(
                id=w.id,
                diary_id=w.diary_id,
                title=w.title,
                list_date=w.list_date,
                status=w.status,
                item_count=counts.get(w.id, 0),
            )
            for w in lists
        ]
    }
