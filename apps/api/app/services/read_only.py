"""Diaries over the owner's plan limit are read-only (oldest diaries stay writable)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary, User
from app.services.tier import limits_for, tier_limit_error


async def _active_diary_ids(db: AsyncSession, owner_user_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(Diary.id)
        .where(Diary.owner_user_id == owner_user_id, Diary.deleted_at.is_(None))
        .order_by(Diary.created_at.asc(), Diary.id.asc())
    )
    return list(result.scalars())


async def read_only_diary_ids(
    db: AsyncSession, owner_user_id: uuid.UUID, tier: str
) -> set[uuid.UUID]:
    """The owner's diaries past the first `limits_for(tier).diaries`, oldest first."""
    ids = await _active_diary_ids(db, owner_user_id)
    return set(ids[limits_for(tier).diaries :])


async def _owner_tier(db: AsyncSession, owner_user_id: uuid.UUID) -> str:
    tier = (
        await db.execute(select(User.subscription_tier).where(User.id == owner_user_id))
    ).scalar_one_or_none()
    return tier or "free"


async def is_diary_read_only(db: AsyncSession, diary: Diary) -> bool:
    tier = await _owner_tier(db, diary.owner_user_id)
    return diary.id in await read_only_diary_ids(db, diary.owner_user_id, tier)


async def ensure_diary_writable(db: AsyncSession, diary: Diary) -> None:
    """Raise the 403 tier_limit error when *diary* is over its owner's plan."""
    tier = await _owner_tier(db, diary.owner_user_id)
    ids = await _active_diary_ids(db, diary.owner_user_id)
    limit = limits_for(tier).diaries
    if diary.id in ids[limit:]:
        raise tier_limit_error(
            source="diary_read_only", limit=limit, current=len(ids), needed=len(ids)
        )
