"""AI drafts spent per owner per calendar month (item 18).

A draft is reserved before the LLM call and released when no AI text is saved.
Rows outlive their entry, so deleting an entry never gives a draft back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiDraftSpend, Diary, LLMGeneration, User
from app.services.tier import limits_for

log = structlog.get_logger()

LOST_DRAFT_AFTER = timedelta(minutes=30)  # a spend this old with no success is lost
AI_DRAFT_LIMIT_ERROR = "ai_draft_limit"  # llm_generations.error when the limit stopped a draft


@dataclass(frozen=True)
class UsagePeriod:
    month: str  # "2026-11"
    resets_on: date  # first day of the next month


async def _owner_timezone(db: AsyncSession, owner_user_id: uuid.UUID) -> tzinfo:
    """The timezone of the owner's oldest active diary, or UTC when there is none."""
    name = (
        await db.execute(
            select(Diary.timezone)
            .where(Diary.owner_user_id == owner_user_id, Diary.deleted_at.is_(None))
            .order_by(Diary.created_at.asc(), Diary.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


async def usage_period(
    db: AsyncSession, owner_user_id: uuid.UUID, now: datetime | None = None
) -> UsagePeriod:
    local = (now or datetime.now(UTC)).astimezone(await _owner_timezone(db, owner_user_id))
    if local.month == 12:
        resets_on = date(local.year + 1, 1, 1)
    else:
        resets_on = date(local.year, local.month + 1, 1)
    return UsagePeriod(month=f"{local.year:04d}-{local.month:02d}", resets_on=resets_on)


async def drafts_used(db: AsyncSession, owner_user_id: uuid.UUID, month: str) -> int:
    count = (
        await db.execute(
            select(func.count())
            .select_from(AiDraftSpend)
            .where(AiDraftSpend.user_id == owner_user_id, AiDraftSpend.month == month)
        )
    ).scalar_one()
    return int(count)


@dataclass(frozen=True)
class AiDraftUsage:
    limit: int | None  # None = unlimited
    used: int
    left: int | None  # None = unlimited; never below 0
    period: UsagePeriod


async def ai_draft_usage(
    db: AsyncSession, owner_user_id: uuid.UUID, tier: str, now: datetime | None = None
) -> AiDraftUsage:
    """The one place that turns a plan limit and this month's spend into 'drafts left'."""
    limit = limits_for(tier).ai_drafts_per_month
    period = await usage_period(db, owner_user_id, now)
    used = await drafts_used(db, owner_user_id, period.month)
    left = None if limit is None else max(0, limit - used)
    return AiDraftUsage(limit=limit, used=used, left=left, period=period)


async def ai_drafts_left(db: AsyncSession, owner: User, now: datetime | None = None) -> int | None:
    """Drafts left this month on the owner's plan; None means unlimited. Never below 0."""
    return (await ai_draft_usage(db, owner.id, owner.subscription_tier, now)).left


async def reserve_ai_draft(
    db: AsyncSession,
    *,
    owner: User,
    entry_id: uuid.UUID | None,
    key: str,
    now: datetime | None = None,
) -> bool:
    """Spend one of the owner's drafts for this month under *key*.

    True when the draft is reserved (or *key* already holds one, so a retried
    task never spends twice); False when the plan's limit is reached. The
    per-owner advisory lock is held until the caller's transaction ends, so
    the reservation must be committed before the LLM call.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('ai_drafts:' || CAST(:uid AS text)))"),
        {"uid": str(owner.id)},
    )
    existing = (
        await db.execute(select(AiDraftSpend.id).where(AiDraftSpend.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return True

    period = await usage_period(db, owner.id, now)
    limit = limits_for(owner.subscription_tier).ai_drafts_per_month
    if limit is not None and await drafts_used(db, owner.id, period.month) >= limit:
        return False

    db.add(
        AiDraftSpend(
            user_id=owner.id, entry_id=entry_id, month=period.month, key=key, sweepable=True
        )
    )
    await db.flush()
    return True


async def release_ai_draft(db: AsyncSession, key: str) -> None:
    """Give back the draft reserved under *key* (no AI text was saved)."""
    await db.execute(delete(AiDraftSpend).where(AiDraftSpend.key == key))


async def release_lost_ai_drafts(db: AsyncSession, now: datetime | None = None) -> int:
    """Give back drafts whose worker was killed before it saved text or released.

    A spend is lost when it is sweepable, older than LOST_DRAFT_AFTER, tied to
    an entry, and no successful generation carries its key. Returns the count.
    """
    cutoff = (now or datetime.now(UTC)) - LOST_DRAFT_AFTER
    succeeded = exists().where(
        LLMGeneration.reservation_key == AiDraftSpend.key, LLMGeneration.status == "success"
    )
    result = await db.execute(
        delete(AiDraftSpend).where(
            AiDraftSpend.sweepable.is_(True),
            AiDraftSpend.created_at < cutoff,
            AiDraftSpend.entry_id.is_not(None),
            ~succeeded,
        )
    )
    count = result.rowcount or 0
    log.info("released_lost_ai_drafts", count=count)
    return count
