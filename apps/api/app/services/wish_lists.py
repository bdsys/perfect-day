"""Wish lists: input rules, role-aware serializers and loaders.

Functions here do not commit; routers do.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User, WishList, WishListClaim, WishListItem
from app.services.public_links import active_link, link_token
from app.services.sharing import person_name

MAX_ITEMS = 100
MAX_CLAIMS_PER_ITEM = 50
MAX_URL_LENGTH = 2048


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
