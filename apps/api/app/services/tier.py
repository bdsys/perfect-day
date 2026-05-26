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
    *,
    owner_user_id: uuid.UUID,
    source: str,
    db: AsyncSession,
    owner_subscription_tier: str = "free",
) -> None:
    """Raise 403 if the user has reached their entry limit for this source type.

    source: 'manual' | 'auto'
    """
    limits = TIER_ENTRY_LIMITS.get(owner_subscription_tier, TIER_ENTRY_LIMITS["free"])
    cap = limits.get(source)
    if cap is None:
        return  # unlimited tier

    from app.models import Diary, Entry

    count_result = await db.execute(
        select(func.count())
        .select_from(Entry)
        .join(Diary, Diary.id == Entry.diary_id)
        .where(
            Diary.owner_user_id == owner_user_id,
            Entry.created_by == source,
            Entry.deleted_at.is_(None),
        )
    )
    current = count_result.scalar_one()

    if current >= cap:
        required_tier = next(
            (t for t, lims in TIER_ENTRY_LIMITS.items() if lims.get(source) is None),
            "tier1",
        )
        raise HTTPException(
            status_code=403,
            detail={
                "code": "tier_limit",
                "details": {
                    "limit": cap,
                    "current": current,
                    "source": source,
                    "required_tier": required_tier,
                },
            },
        )


async def try_enforce_entry_tier_limit(
    *,
    owner_user_id: uuid.UUID,
    source: str,
    db: AsyncSession,
    owner_subscription_tier: str = "free",
) -> tuple[bool, str | None]:
    """No-raise variant of enforce_entry_tier_limit for use in Celery workers.

    Returns (True, None) if within limit, (False, reason_str) if over limit.
    Catches HTTPException only — DB/infrastructure errors are intentionally
    allowed to propagate so the worker surfaces real failures.
    """
    try:
        await enforce_entry_tier_limit(
            owner_user_id=owner_user_id,
            source=source,
            db=db,
            owner_subscription_tier=owner_subscription_tier,
        )
        return True, None
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and "details" in detail:
            d = detail["details"]
            reason_str = (
                f"{detail.get('code', 'tier_limit')}: "
                f"{d.get('source', '?')} limit {d.get('current', '?')}/{d.get('limit', '?')}"
            )
        else:
            reason_str = str(detail)
        return False, reason_str
