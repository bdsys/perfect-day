"""How many more photos an entry may take on its diary owner's plan."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, EntryPhoto, User
from app.services.tier import limits_for, tier_limit_error


async def photo_room(
    db: AsyncSession, entry_id: uuid.UUID, owner: User, *, lock: bool = True
) -> int | None:
    """Photos the entry can still take; None when the plan is unlimited.

    *owner* is always the diary owner, never the caller. With *lock* the entry
    row is held ``FOR UPDATE`` until the caller commits, so two attaches to the
    same entry cannot both see room for the last photo.
    """
    limit = limits_for(owner.subscription_tier).photos_per_entry
    if limit is None:
        return None
    if lock:
        await db.execute(select(Entry.id).where(Entry.id == entry_id).with_for_update())
    attached = await _attached(db, entry_id)
    return max(0, limit - attached)


async def _attached(db: AsyncSession, entry_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(EntryPhoto).where(EntryPhoto.entry_id == entry_id)
            )
        ).scalar_one()
    )


async def ensure_photo_room(
    db: AsyncSession, entry_id: uuid.UUID, owner: User, *, lock: bool = True
) -> None:
    """Raise the 403 ``tier_limit`` when the entry cannot take another photo."""
    room = await photo_room(db, entry_id, owner, lock=lock)
    if room != 0:
        return
    limit = limits_for(owner.subscription_tier).photos_per_entry
    assert limit is not None  # room == 0 only on a limited plan
    attached = await _attached(db, entry_id)
    raise tier_limit_error(source="photo", limit=limit, current=attached, needed=attached + 1)
