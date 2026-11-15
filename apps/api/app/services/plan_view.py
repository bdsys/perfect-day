"""The plan and usage blocks shown in /me/plan and on the diary and entry detail responses."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary, User
from app.services.ai_drafts import drafts_used, usage_period
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


def _tier_of(user: User) -> str:
    return user.subscription_tier if user.subscription_tier in PLAN_NAMES else "free"


async def owner_plan_out(db: AsyncSession, diary: Diary) -> OwnerPlanOut:
    owner = await db.get(User, diary.owner_user_id)
    tier = _tier_of(owner) if owner else "free"
    limits = limits_for(tier)
    period = await usage_period(db, diary.owner_user_id)
    left: int | None = None
    if limits.ai_drafts_per_month is not None:
        used = await drafts_used(db, diary.owner_user_id, period.month)
        left = max(0, limits.ai_drafts_per_month - used)
    read_only = diary.id in await read_only_diary_ids(db, diary.owner_user_id, tier)
    return OwnerPlanOut(
        tier=tier,
        plan_name=PLAN_NAMES[tier],
        ai_drafts_limit=limits.ai_drafts_per_month,
        ai_drafts_left=left,
        resets_on=period.resets_on,
        photos_per_entry=limits.photos_per_entry,
        read_only=read_only,
    )


async def my_plan_out(db: AsyncSession, user: User) -> MyPlanOut:
    tier = _tier_of(user)
    limits = limits_for(tier)
    period = await usage_period(db, user.id)
    used = await drafts_used(db, user.id, period.month)
    diaries_used = (
        await db.execute(
            select(func.count())
            .select_from(Diary)
            .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
        )
    ).scalar_one()
    left = None if limits.ai_drafts_per_month is None else max(0, limits.ai_drafts_per_month - used)
    return MyPlanOut(
        tier=tier,
        plan_name=PLAN_NAMES[tier],
        diaries_limit=limits.diaries,
        diaries_used=int(diaries_used),
        ai_drafts_limit=limits.ai_drafts_per_month,
        ai_drafts_used=used,
        ai_drafts_left=left,
        resets_on=period.resets_on,
        photos_per_entry=limits.photos_per_entry,
        read_only_diary_ids=sorted(await read_only_diary_ids(db, user.id, tier)),
    )
