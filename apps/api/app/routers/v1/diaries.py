from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import AuditLog, Diary, DiaryPermission, ScanJob, User

router = APIRouter(prefix="/diaries", tags=["diaries"])

TIER_DIARY_LIMITS = {"free": 1, "tier1": 2, "tier2": 4}


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80] or "diary"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DiaryCreate(BaseModel):
    name: str
    timezone: str
    subject_name: str | None = None
    subject_relation: str = "self"
    tone_hint: str = "warm, narrative"
    scan_interval_minutes: int = 60


class DiaryPatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    subject_name: str | None = None
    subject_relation: str | None = None
    voice_override: str | None = None
    tone_hint: str | None = None
    timezone: str | None = None
    scan_interval_minutes: int | None = None
    scan_enabled: bool | None = None
    notifications_muted: bool | None = None
    lat: Annotated[Decimal | None, Field(ge=-90, le=90)] = None
    lon: Annotated[Decimal | None, Field(ge=-180, le=180)] = None


class DiaryOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    subject_name: str | None
    subject_relation: str
    scan_enabled: bool
    scan_interval_minutes: int
    lat: Decimal | None = None
    lon: Decimal | None = None
    deleted_at: datetime | None
    hard_delete_after: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_diary_or_404(
    diary_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    require_owner: bool = False,
) -> tuple[Diary, str | None]:
    """Return (diary, role) or raise 404. role is None for owner."""
    result = await db.execute(select(Diary).where(Diary.id == diary_id, Diary.deleted_at.is_(None)))
    diary = result.scalar_one_or_none()
    if diary is None:
        raise HTTPException(status_code=404, detail="not_found")

    if diary.owner_user_id == user.id:
        if require_owner:
            return diary, None
        return diary, None

    # Check permissions
    perm_result = await db.execute(
        select(DiaryPermission).where(
            DiaryPermission.diary_id == diary_id,
            DiaryPermission.user_id == user.id,
        )
    )
    perm = perm_result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="not_found")
    if require_owner:
        raise HTTPException(status_code=403, detail="forbidden")
    return diary, perm.role


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DiaryOut])
async def list_diaries(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Diary]:
    owned = await db.execute(
        select(Diary).where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
    )
    shared_ids = await db.execute(
        select(DiaryPermission.diary_id).where(DiaryPermission.user_id == user.id)
    )
    shared_diary_ids = [r[0] for r in shared_ids.fetchall()]
    shared = await db.execute(
        select(Diary).where(Diary.id.in_(shared_diary_ids), Diary.deleted_at.is_(None))
    )
    return list(owned.scalars()) + list(shared.scalars())


@router.post("", response_model=DiaryOut, status_code=status.HTTP_201_CREATED)
async def create_diary(
    body: DiaryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Diary:
    limit = TIER_DIARY_LIMITS.get(user.subscription_tier, 1)

    # Advisory lock to prevent check-then-create race
    await db.execute(
        text(f"SELECT pg_advisory_xact_lock({hash(str(user.id)) & 0x7FFFFFFF})")
    )
    count_result = await db.execute(
        select(func.count())
        .select_from(Diary)
        .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
    )
    current = count_result.scalar_one()
    if current >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "tier_limit",
                "details": {"limit": limit, "current": current, "source": "diary", "required_tier": "tier1"},
            },
        )

    slug = _slugify(body.name)
    # Ensure unique slug per owner
    base_slug = slug
    suffix = 1
    while True:
        exists = await db.execute(
            select(Diary).where(Diary.owner_user_id == user.id, Diary.slug == slug)
        )
        if exists.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    diary = Diary(
        owner_user_id=user.id,
        name=body.name,
        slug=slug,
        timezone=body.timezone,
        subject_name=body.subject_name,
        subject_relation=body.subject_relation,
        tone_hint=body.tone_hint,
        scan_interval_minutes=body.scan_interval_minutes,
    )
    db.add(diary)
    await db.flush()

    # Create 1:1 scan job
    db.add(ScanJob(diary_id=diary.id))
    await db.refresh(diary)
    # Commit before returning so the row is visible to concurrent requests that
    # receive this response and immediately issue follow-up calls (e.g. DELETE).
    await db.commit()
    return diary


@router.get("/trash", response_model=list[DiaryOut])
async def list_deleted_diaries(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Diary]:
    result = await db.execute(
        select(Diary)
        .where(
            Diary.owner_user_id == user.id,
            Diary.deleted_at.is_not(None),
            Diary.hard_delete_after > datetime.now(tz=UTC),
        )
        .order_by(Diary.hard_delete_after.asc())
    )
    return list(result.scalars())


@router.get("/{diary_id}", response_model=DiaryOut)
async def get_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Diary:
    diary, _ = await _get_diary_or_404(diary_id, user, db)
    return diary


@router.patch("/{diary_id}", response_model=DiaryOut)
async def patch_diary(
    diary_id: uuid.UUID,
    body: DiaryPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Diary:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(diary, field, value)

    await db.commit()
    await db.refresh(diary)
    return diary


@router.delete("/{diary_id}", response_model=DiaryOut)
async def delete_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Diary:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    now = datetime.now(tz=UTC)
    diary.deleted_at = now
    diary.hard_delete_after = now + timedelta(days=30)
    diary.scan_enabled = False
    db.add(
        AuditLog(user_id=user.id, action="diary.delete", target_type="diary", target_id=diary.id)
    )
    return diary


@router.post("/{diary_id}/restore", response_model=DiaryOut)
async def restore_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Diary:
    # Fetch even soft-deleted diaries for restore
    result = await db.execute(
        select(Diary).where(Diary.id == diary_id, Diary.owner_user_id == user.id)
    )
    diary = result.scalar_one_or_none()
    if diary is None:
        raise HTTPException(status_code=404, detail="not_found")
    if diary.hard_delete_after and diary.hard_delete_after < datetime.now(tz=UTC):
        raise HTTPException(status_code=410, detail="grace_period_expired")
    # Check tier limit before restoring
    limit = TIER_DIARY_LIMITS.get(user.subscription_tier, 1)
    await db.execute(
        text(f"SELECT pg_advisory_xact_lock({hash(str(user.id)) & 0x7FFFFFFF})")
    )
    count_result = await db.execute(
        select(func.count())
        .select_from(Diary)
        .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
    )
    current = count_result.scalar_one()
    if current >= limit:
        required_tier = next(
            (t for t, lim in TIER_DIARY_LIMITS.items() if lim > limit),
            "tier1",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "tier_limit",
                "details": {
                    "limit": limit,
                    "current": current,
                    "source": "diary",
                    "required_tier": required_tier,
                },
            },
        )
    # If the slug is now taken by a different active diary, assign a new unique one
    slug = diary.slug
    base_slug = slug
    suffix = 1
    while True:
        conflict = await db.execute(
            select(Diary).where(
                Diary.owner_user_id == user.id,
                Diary.slug == slug,
                Diary.id != diary_id,
                Diary.deleted_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    diary.slug = slug
    diary.deleted_at = None
    diary.hard_delete_after = None
    diary.scan_enabled = True
    return diary

