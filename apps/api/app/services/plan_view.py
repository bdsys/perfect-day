"""The plan and usage blocks shown in /me/plan and on the diary and entry detail responses."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary, User
from app.services.ai_drafts import ai_draft_usage
from app.services.read_only import read_only_diary_ids
from app.services.tier import PLAN_NAMES, limits_for


class OwnerPlanOut(BaseModel):
    """The diary owner's plan: whose quota every co-parent's actions use."""

    tier: str
    plan_name: str
    ai_drafts_limit: int | None
    ai_drafts_left: int | None
    resets_on: date
    photos_per_entry: int | None
    read_only: bool
    backfill: bool
    scan_past_days_max: int | None


class MyPlanOut(BaseModel):
    tier: str
    plan_name: str
    diaries_limit: int
    diaries_used: int
    ai_drafts_limit: int | None
    ai_drafts_used: int
    ai_drafts_left: int | None
    resets_on: date
    photos_per_entry: int | None
    read_only_diary_ids: list[uuid.UUID]
    backfill: bool
    scan_past_days_max: int | None


def _tier_of(user: User) -> str:
    return user.subscription_tier if user.subscription_tier in PLAN_NAMES else "free"


async def owner_plan_out(db: AsyncSession, diary: Diary) -> OwnerPlanOut:
    owner = await db.get(User, diary.owner_user_id)
    tier = _tier_of(owner) if owner else "free"
    limits = limits_for(tier)
    usage = await ai_draft_usage(db, diary.owner_user_id, tier)
    read_only = diary.id in await read_only_diary_ids(db, diary.owner_user_id, tier)
    return OwnerPlanOut(
        tier=tier,
        plan_name=PLAN_NAMES[tier],
        ai_drafts_limit=limits.ai_drafts_per_month,
        ai_drafts_left=usage.left,
        resets_on=usage.period.resets_on,
        photos_per_entry=limits.photos_per_entry,
        read_only=read_only,
        backfill=limits.backfill,
        scan_past_days_max=limits.scan_past_days,
    )


async def my_plan_out(db: AsyncSession, user: User) -> MyPlanOut:
    tier = _tier_of(user)
    limits = limits_for(tier)
    usage = await ai_draft_usage(db, user.id, tier)
    diaries_used = (
        await db.execute(
            select(func.count())
            .select_from(Diary)
            .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
        )
    ).scalar_one()
    return MyPlanOut(
        tier=tier,
        plan_name=PLAN_NAMES[tier],
        diaries_limit=limits.diaries,
        diaries_used=int(diaries_used),
        ai_drafts_limit=limits.ai_drafts_per_month,
        ai_drafts_used=usage.used,
        ai_drafts_left=usage.left,
        resets_on=usage.period.resets_on,
        photos_per_entry=limits.photos_per_entry,
        read_only_diary_ids=sorted(await read_only_diary_ids(db, user.id, tier)),
        backfill=limits.backfill,
        scan_past_days_max=limits.scan_past_days,
    )
