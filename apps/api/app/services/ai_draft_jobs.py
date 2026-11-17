"""Queued and running AI drafts, for the "AI is working on this draft" indicator (row 38).

A job row exists from the moment a draft is queued until it finishes. It never
writes to `entries`, so `updated_at` is untouched. Callers commit; the indicator
is best-effort, so callers log and carry on when these calls fail.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiDraftJob, Entry
from app.services.ai_drafts import LOST_DRAFT_AFTER

AiStatus = Literal["queued", "writing"]


async def add_job(
    db: AsyncSession,
    entry_id: uuid.UUID,
    *,
    delay_seconds: int = 0,
    job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a queued job that is due `delay_seconds` after it was queued."""
    job_id = job_id or uuid.uuid4()
    now = datetime.now(UTC)
    db.add(
        AiDraftJob(
            id=job_id,
            entry_id=entry_id,
            queued_at=now,
            run_after=now + timedelta(seconds=delay_seconds),
        )
    )
    await db.flush()
    return job_id


async def mark_started(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(
        update(AiDraftJob).where(AiDraftJob.id == job_id).values(started_at=func.now())
    )


async def finish_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(delete(AiDraftJob).where(AiDraftJob.id == job_id))


async def sweep_stale_jobs(db: AsyncSession, now: datetime | None = None) -> int:
    """Delete jobs whose worker is gone: started, or due, more than LOST_DRAFT_AFTER ago."""
    cutoff = (now or datetime.now(UTC)) - LOST_DRAFT_AFTER
    result = await db.execute(
        delete(AiDraftJob).where(
            func.coalesce(AiDraftJob.started_at, AiDraftJob.run_after) < cutoff
        )
    )
    return cast(CursorResult, result).rowcount


def _statuses(rows) -> dict[uuid.UUID, AiStatus]:
    return {entry_id: "writing" if writing else "queued" for entry_id, writing in rows}


async def ai_statuses(
    db: AsyncSession, entry_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, AiStatus]:
    """Entries with a started job are "writing", with only waiting jobs "queued"; others absent."""
    if not entry_ids:
        return {}
    rows = await db.execute(
        select(AiDraftJob.entry_id, func.bool_or(AiDraftJob.started_at.is_not(None)))
        .where(AiDraftJob.entry_id.in_(entry_ids))
        .group_by(AiDraftJob.entry_id)
    )
    return _statuses(rows.all())


async def diary_ai_statuses(
    db: AsyncSession, diary_id: uuid.UUID, *, published_only: bool
) -> dict[uuid.UUID, AiStatus]:
    stmt = (
        select(AiDraftJob.entry_id, func.bool_or(AiDraftJob.started_at.is_not(None)))
        .join(Entry, Entry.id == AiDraftJob.entry_id)
        .where(Entry.diary_id == diary_id, Entry.deleted_at.is_(None))
        .group_by(AiDraftJob.entry_id)
    )
    if published_only:
        stmt = stmt.where(Entry.status == "published")
    return _statuses((await db.execute(stmt)).all())
