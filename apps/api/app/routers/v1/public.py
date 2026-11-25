"""Link-only public API: a wish list opened, claimed and unclaimed by anyone with the link.

These routes never authenticate: no `get_current_user`, no `request.state.user`. A
bearer header changes nothing. Every dead link answers 404 `link_inactive`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import PUBLIC_READ_LIMIT, PUBLIC_WRITE_LIMIT, public_limiter
from app.models import Diary, WishList
from app.services.public_links import LINK_INACTIVE, link_expired, resolve_link
from app.services.wish_lists import claim_item, load_list, shared_by_name, undo_claim

router = APIRouter(prefix="/public", tags=["public"])


class PublicItemOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str | None
    note: str | None
    many: bool
    taken: bool
    claim_count: int


class PublicWishListOut(BaseModel):
    title: str
    list_date: date
    pasted_list_url: str | None
    shared_by: str | None
    items: list[PublicItemOut]


class PublicPageOut(BaseModel):
    kind: Literal["wish_list"]
    wish_list: PublicWishListOut


class PublicClaimIn(BaseModel):
    # A body-size bound only; claim_item enforces 1-60 characters after trimming.
    name: str = Field(max_length=200)


class PublicClaimOut(BaseModel):
    claim_id: uuid.UUID
    secret: str


async def _live_list(db: AsyncSession, token: str) -> tuple[WishList, Diary]:
    """The wish list behind a token, or 404 `link_inactive` for any dead state."""
    link, diary = await resolve_link(db, token)
    if link.kind != "wish_list":
        raise HTTPException(status_code=404, detail=LINK_INACTIVE)
    try:
        wl = await load_list(db, link.target_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail=LINK_INACTIVE) from None
    if wl.status != "published" or link_expired(wl.list_date, diary.timezone):
        raise HTTPException(status_code=404, detail=LINK_INACTIVE)
    return wl, diary


@router.get("/{token}", response_model=PublicPageOut)
@public_limiter.limit(PUBLIC_READ_LIMIT)
async def read_public_page(
    request: Request, token: str, db: AsyncSession = Depends(get_db, scope="function")
) -> PublicPageOut:
    wl, diary = await _live_list(db, token)
    shared_by = await shared_by_name(db, diary.id)
    items = []
    for i in wl.items:
        n = len(i.claims)
        # Same rule as the in-app serializer (services.wish_lists.item_out).
        items.append(
            PublicItemOut(
                id=i.id,
                name=i.name,
                url=i.url,
                note=i.note,
                many=i.many,
                taken=(not i.many) and n >= 1,
                claim_count=n,
            )
        )
    return PublicPageOut(
        kind="wish_list",
        wish_list=PublicWishListOut(
            title=wl.title,
            list_date=wl.list_date,
            pasted_list_url=wl.pasted_list_url,
            shared_by=shared_by,
            items=items,
        ),
    )


@router.post("/{token}/items/{item_id}/claims", response_model=PublicClaimOut, status_code=201)
@public_limiter.limit(PUBLIC_WRITE_LIMIT)
async def public_claim(
    request: Request,
    token: str,
    item_id: uuid.UUID,
    body: PublicClaimIn,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PublicClaimOut:
    wl, _ = await _live_list(db, token)
    if item_id not in {i.id for i in wl.items}:
        raise HTTPException(status_code=404, detail=LINK_INACTIVE)
    claim, secret = await claim_item(db, item_id, name=body.name)
    await db.commit()
    assert secret is not None
    return PublicClaimOut(claim_id=claim.id, secret=secret)


@router.delete("/{token}/claims/{claim_id}", status_code=204)
@public_limiter.limit(PUBLIC_WRITE_LIMIT)
async def public_undo(
    request: Request,
    token: str,
    claim_id: uuid.UUID,
    x_claim_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    wl, _ = await _live_list(db, token)
    claim_ids = {c.id for i in wl.items for c in i.claims}
    if claim_id not in claim_ids:
        raise HTTPException(status_code=404, detail="claim_not_found")
    await undo_claim(db, claim_id, secret=x_claim_secret)
    await db.commit()
    return Response(status_code=204)
