"""Tier/entitlement enforcement helpers."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

TIER_ENTRY_LIMITS: dict[str, dict[str, int | None]] = {
    "free": {"manual": 5, "auto": 3},
    "tier1": {"manual": None, "auto": None},
    "tier2": {"manual": None, "auto": None},
}


async def enforce_entry_tier_limit(
    user_id: uuid.UUID,
    diary_id: uuid.UUID,
    source: str,
    db: AsyncSession,
    subscription_tier: str = "free",
) -> None:
    """Raise 402 if the user has reached their entry limit for this source type.

    source: 'manual' | 'auto'
    """
    limits = TIER_ENTRY_LIMITS.get(subscription_tier, TIER_ENTRY_LIMITS["free"])
    cap = limits.get(source)
    if cap is None:
        return  # unlimited tier

    from app.models import Diary, Entry

    count_result = await db.execute(
        select(func.count())
        .select_from(Entry)
        .join(Diary, Diary.id == Entry.diary_id)
        .where(
            Diary.owner_user_id == user_id,
            Entry.created_by == source,
            Entry.deleted_at.is_(None),
        )
    )
    current = count_result.scalar_one()

    if current >= cap:
        label = "Manual entry" if source == "manual" else "Auto-generated entry"
        raise HTTPException(
            status_code=402,
            detail=(
                f"{label} limit reached for {subscription_tier} tier "
                f"({current}/{cap}). Upgrade to create more."
            ),
        )
