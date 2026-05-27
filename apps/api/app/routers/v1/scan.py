"""Scan API endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.dependencies import get_redis
from app.models import BackfillRun, ScanJob, ScanRun, User
from app.routers.v1.diaries import _get_diary_or_404

router = APIRouter(tags=["scan"])


class ScanRunRequest(BaseModel):
    past_days: int | None = Field(default=None, ge=1, le=3650)
    future_days: int | None = Field(default=None, ge=1, le=3650)


class ScanRunOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    triggered_by: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    events_calendar: int
    entries_created: int

    model_config = {"from_attributes": True}


@router.get("/diaries/{diary_id}/scan")
async def get_scan_config(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    diary, _ = await _get_diary_or_404(diary_id, user, db)
    result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
    job = result.scalar_one_or_none()
    return {
        "scan_enabled": diary.scan_enabled,
        "scan_interval_minutes": diary.scan_interval_minutes,
        "last_scan_status": job.last_scan_status if job else None,
        "next_scan_after": job.next_scan_after if job else None,
        "consecutive_failures": job.consecutive_failures if job else 0,
    }


@router.post("/diaries/{diary_id}/scan/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scan(
    diary_id: uuid.UUID,
    body: ScanRunRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)

    r = get_redis()
    lock_key = f"scan_lock:{diary_id}"
    if await r.exists(lock_key):
        raise HTTPException(
            status_code=409,
            detail="scan_in_progress",
            headers={"Retry-After": "60"},
        )

    from app.workers.tasks import scan_diary

    # Reset backoff so the on-demand scan isn't suppressed
    result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
    job = result.scalar_one_or_none()
    if job:
        job.next_scan_after = None
    past = body.past_days if body else None
    future = body.future_days if body else None
    scan_diary.delay(str(diary_id), past_days=past, future_days=future)
    return {"queued": True}


@router.get("/diaries/{diary_id}/scan/runs", response_model=list[ScanRunOut])
async def list_scan_runs(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ScanRun]:
    await _get_diary_or_404(diary_id, user, db)
    result = await db.execute(
        select(ScanRun)
        .where(ScanRun.diary_id == diary_id)
        .order_by(ScanRun.started_at.desc())
        .limit(50)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


ALLOWED_BACKFILL_SOURCES = {"google_calendar"}


class BackfillRequest(BaseModel):
    from_date: date
    to_date: date
    sources: list[str] = Field(default_factory=lambda: ["google_calendar"])

    @model_validator(mode="after")
    def _validate_range_and_sources(self) -> BackfillRequest:
        if self.from_date > self.to_date:
            raise ValueError("from_date must be <= to_date")
        if (self.to_date - self.from_date).days > 365:
            raise ValueError("range must be <= 365 days")
        unknown = set(self.sources) - ALLOWED_BACKFILL_SOURCES
        if unknown or not self.sources:
            raise ValueError(f"unsupported sources: {sorted(unknown) or 'empty'}")
        return self


class BackfillRunOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    from_date: date
    to_date: date
    sources: list[str]
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    events_ingested: int
    entries_created: int
    error: str | None

    model_config = {"from_attributes": True}


@router.post(
    "/diaries/{diary_id}/scan/backfill",
    response_model=BackfillRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_backfill(
    diary_id: uuid.UUID,
    body: BackfillRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BackfillRun:
    await _get_diary_or_404(diary_id, user, db, require_owner=True)

    backfill_run = BackfillRun(
        diary_id=diary_id,
        from_date=body.from_date,
        to_date=body.to_date,
        sources=body.sources,
        status="pending",
    )
    db.add(backfill_run)
    await db.flush()
    await db.refresh(backfill_run)

    from app.workers.tasks import backfill_diary

    backfill_diary.delay(str(backfill_run.id))
    return backfill_run
