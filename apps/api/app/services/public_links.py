"""Public links: link-only pages reached by an unguessable token.

The token is `secrets.token_urlsafe(32)`. Lookup is by SHA-256 of the token; an
AES-GCM copy lets the owner copy the link again. Functions here do not commit.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import deletion_state
from app.core.security import decrypt_secret, encrypt_secret, hash_token
from app.models import Diary, PublicLink, User

LinkKind = Literal["wish_list"]
LINK_INACTIVE = "link_inactive"
LINK_GRACE_DAYS = 7


def _inactive() -> HTTPException:
    return HTTPException(status_code=404, detail=LINK_INACTIVE)


async def revoke_links(db: AsyncSession, *, kind: LinkKind, target_id: uuid.UUID) -> None:
    await db.execute(
        update(PublicLink)
        .where(
            PublicLink.kind == kind,
            PublicLink.target_id == target_id,
            PublicLink.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(tz=UTC))
    )


async def create_link(
    db: AsyncSession, *, diary_id: uuid.UUID, kind: LinkKind, target_id: uuid.UUID
) -> tuple[PublicLink, str]:
    """Revoke any active link for the target, then make a new one."""
    await revoke_links(db, kind=kind, target_id=target_id)
    raw = secrets.token_urlsafe(32)
    link = PublicLink(
        diary_id=diary_id,
        kind=kind,
        target_id=target_id,
        token_hash=hash_token(raw),
        token_encrypted=encrypt_secret(raw),
    )
    db.add(link)
    await db.flush()
    return link, raw


async def active_link(
    db: AsyncSession, *, kind: LinkKind, target_id: uuid.UUID
) -> PublicLink | None:
    return (
        await db.execute(
            select(PublicLink)
            .where(
                PublicLink.kind == kind,
                PublicLink.target_id == target_id,
                PublicLink.revoked_at.is_(None),
            )
            .order_by(PublicLink.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def link_token(link: PublicLink) -> str:
    return decrypt_secret(link.token_encrypted)


async def resolve_link(db: AsyncSession, token: str) -> tuple[PublicLink, Diary]:
    """The link and its diary, or 404 `link_inactive` for every dead state.

    Target checks (published, date) belong to the caller.
    """
    link = (
        await db.execute(select(PublicLink).where(PublicLink.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if link is None or link.revoked_at is not None:
        raise _inactive()
    diary = (
        await db.execute(select(Diary).where(Diary.id == link.diary_id, Diary.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if diary is None:
        raise _inactive()
    owner = (
        await db.execute(select(User).where(User.id == diary.owner_user_id))
    ).scalar_one_or_none()
    if owner is None or deletion_state(owner) != "active":
        raise _inactive()
    return link, diary


def link_expired(list_date: date, diary_tz: str, now: datetime | None = None) -> bool:
    """True once the diary's local date is more than 7 days past `list_date`."""
    now = now or datetime.now(tz=UTC)
    local_today = now.astimezone(ZoneInfo(diary_tz)).date()
    return local_today > list_date + timedelta(days=LINK_GRACE_DAYS)
