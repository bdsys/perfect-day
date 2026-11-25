"""Wish lists: input rules, role-aware serializers and loaders.

Functions here do not commit; routers do.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_token
from app.models import Diary, User, WishList, WishListClaim, WishListItem
from app.services.public_links import active_link, link_token
from app.services.sharing import person_name

MAX_ITEMS = 100
MAX_CLAIMS_PER_ITEM = 50
MAX_URL_LENGTH = 2048
MAX_NAME_LENGTH = 60
COMING_UP_DAYS = 42


def clean_url(v: str | None) -> str | None:
    """Trim; keep only http(s) URLs with a host; lower-case the scheme. Blank is None."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > MAX_URL_LENGTH:
        raise ValueError("url_too_long")
    if any(ord(c) < 33 or ord(c) == 127 for c in v):
        raise ValueError("invalid_url")
    parts = urlsplit(v)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("invalid_url")
    return scheme + v[len(parts.scheme) :]


Url = Annotated[str | None, AfterValidator(clean_url)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ItemName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Note = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)]


class WishListCreate(BaseModel):
    title: Title
    list_date: date
    pasted_list_url: Url = None


class WishListPatch(BaseModel):
    title: Title | None = None
    list_date: date | None = None
    pasted_list_url: Url = None


class ItemCreate(BaseModel):
    name: ItemName
    url: Url = None
    note: Note = None
    many: bool = False


class ItemPatch(BaseModel):
    name: ItemName | None = None
    url: Url = None
    note: Note = None
    many: bool | None = None


class ClaimOut(BaseModel):
    id: uuid.UUID
    name: str
    signed_in: bool
    created_at: datetime


class ItemOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str | None
    note: str | None
    many: bool
    taken: bool
    claim_count: int
    mine: bool
    claims: list[ClaimOut] | None


class LinkOut(BaseModel):
    token: str
    path: str


class WishListOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    title: str
    list_date: date
    pasted_list_url: str | None
    status: str
    items: list[ItemOut]
    link: LinkOut | None = None


class WishListSummaryOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    title: str
    list_date: date
    status: str
    item_count: int = Field(ge=0)


_LOAD = (
    selectinload(WishList.items).selectinload(WishListItem.claims).selectinload(WishListClaim.user),
)


async def load_list(db: AsyncSession, wish_list_id: uuid.UUID, *, lock: bool = False) -> WishList:
    """The list with items and claims, or 404. `lock` takes FOR UPDATE on the list row."""
    stmt = (
        select(WishList)
        .where(WishList.id == wish_list_id)
        .options(*_LOAD)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update(of=WishList)
    wl = (await db.execute(stmt)).scalar_one_or_none()
    if wl is None:
        raise HTTPException(status_code=404, detail="not_found")
    return wl


async def load_item(db: AsyncSession, item_id: uuid.UUID) -> WishListItem:
    item = (
        await db.execute(select(WishListItem).where(WishListItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="not_found")
    return item


def _claim_out(c: WishListClaim) -> ClaimOut:
    signed_in = c.user is not None
    name = person_name(c.user) if c.user is not None else (c.name or "")
    return ClaimOut(id=c.id, name=name, signed_in=signed_in, created_at=c.created_at)


def item_out(item: WishListItem, *, caller_id: uuid.UUID, manager: bool) -> ItemOut:
    claims = list(item.claims)
    return ItemOut(
        id=item.id,
        name=item.name,
        url=item.url,
        note=item.note,
        many=item.many,
        taken=(not item.many) and len(claims) >= 1,
        claim_count=len(claims),
        mine=any(c.user_id == caller_id for c in claims),
        claims=[_claim_out(c) for c in claims] if manager else None,
    )


async def wish_list_out(
    db: AsyncSession, wl: WishList, *, caller: User, manager: bool
) -> WishListOut:
    link = None
    if manager:
        row = await active_link(db, kind="wish_list", target_id=wl.id)
        if row is not None:
            token = link_token(row)
            link = LinkOut(token=token, path=f"/p/{token}")
    return WishListOut(
        id=wl.id,
        diary_id=wl.diary_id,
        title=wl.title,
        list_date=wl.list_date,
        pasted_list_url=wl.pasted_list_url,
        status=wl.status,
        items=[item_out(i, caller_id=caller.id, manager=manager) for i in wl.items],
        link=link,
    )


async def load_item_full(db: AsyncSession, item_id: uuid.UUID) -> WishListItem:
    """The item with its claims (and their users), refreshed from the database."""
    return (
        await db.execute(
            select(WishListItem)
            .where(WishListItem.id == item_id)
            .options(selectinload(WishListItem.claims).selectinload(WishListClaim.user))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def claim_item(
    db: AsyncSession,
    item_id: uuid.UUID,
    *,
    user: User | None = None,
    name: str | None = None,
) -> tuple[WishListClaim, str | None]:
    """Claim an item as a signed-in user, or by name (the public link).

    Returns the claim and, for a public claim, the raw undo secret (only its hash is
    stored). Locks the item row, so two claims on one item cannot both win. Does not
    commit. The caller has already checked the list is published and visible.
    """
    if (user is None) == (name is None):
        raise ValueError("claim_item needs exactly one of user or name")
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            raise HTTPException(status_code=422, detail="invalid_name")
    item = (
        await db.execute(
            select(WishListItem)
            .where(WishListItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="not_found")
    count = (
        await db.execute(
            select(func.count()).select_from(WishListClaim).where(WishListClaim.item_id == item.id)
        )
    ).scalar_one()
    if user is not None:
        mine = (
            await db.execute(
                select(WishListClaim.id).where(
                    WishListClaim.item_id == item.id, WishListClaim.user_id == user.id
                )
            )
        ).first()
        if mine is not None:
            raise HTTPException(status_code=409, detail="already_claimed")
    if not item.many and count >= 1:
        raise HTTPException(status_code=409, detail="item_taken")
    if count >= MAX_CLAIMS_PER_ITEM:
        raise HTTPException(status_code=409, detail="item_claims_full")
    secret = secrets.token_urlsafe(24) if name is not None else None
    claim = WishListClaim(
        item_id=item.id,
        user_id=user.id if user is not None else None,
        name=name,
        secret_hash=hash_token(secret) if secret is not None else None,
    )
    try:
        async with db.begin_nested():
            db.add(claim)
    except IntegrityError as e:  # backstop for the (item, user) unique index
        raise HTTPException(status_code=409, detail="already_claimed") from e
    return claim, secret


async def undo_claim(
    db: AsyncSession,
    claim_id: uuid.UUID,
    *,
    user: User | None = None,
    secret: str | None = None,
) -> None:
    """Remove a claim the caller owns: by signed-in user, or by the public secret."""
    claim = (
        await db.execute(select(WishListClaim).where(WishListClaim.id == claim_id))
    ).scalar_one_or_none()
    owned = False
    if claim is not None:
        if user is not None and claim.user_id == user.id:
            owned = True
        elif secret and claim.secret_hash is not None:
            owned = hmac.compare_digest(claim.secret_hash, hash_token(secret))
    if not owned:
        raise HTTPException(status_code=404, detail="claim_not_found")
    await db.execute(delete(WishListClaim).where(WishListClaim.id == claim_id))


async def coming_up(db: AsyncSession, diary: Diary, now: datetime | None = None) -> list[WishList]:
    """Published lists dated today..today+42 days in the diary's timezone, soonest first."""
    now = now or datetime.now(tz=UTC)
    today = now.astimezone(ZoneInfo(diary.timezone)).date()
    rows = await db.execute(
        select(WishList)
        .where(
            WishList.diary_id == diary.id,
            WishList.status == "published",
            WishList.list_date >= today,
            WishList.list_date <= today + timedelta(days=COMING_UP_DAYS),
        )
        .order_by(WishList.list_date, WishList.created_at)
    )
    return list(rows.scalars())
