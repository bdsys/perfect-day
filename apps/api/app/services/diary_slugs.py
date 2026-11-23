"""A diary slug that is free for its owner."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary


async def unique_slug(
    db: AsyncSession,
    owner_user_id: uuid.UUID,
    base: str,
    *,
    exclude_diary_id: uuid.UUID | None = None,
) -> str:
    """*base*, or *base*-1, -2, ... : the first no other diary of this owner uses.

    Checks every row of the owner, soft-deleted ones included, because
    uq_diaries_owner_slug covers them all.
    """
    query = select(Diary.slug).where(Diary.owner_user_id == owner_user_id)
    if exclude_diary_id is not None:
        query = query.where(Diary.id != exclude_diary_id)
    taken = set((await db.execute(query)).scalars())
    slug = base
    suffix = 1
    while slug in taken:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug
