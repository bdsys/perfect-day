"""ai_draft_jobs: the "AI is working on this draft" rows (row 38)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiDraftJob, Entry
from app.services.ai_draft_jobs import (
    add_job,
    ai_statuses,
    diary_ai_statuses,
    finish_job,
    mark_started,
    sweep_stale_jobs,
)
from tests.fixtures.factories import make_diary, make_entry, make_user


async def _entry(db: AsyncSession, **kwargs) -> Entry:
    owner = await make_user(db)
    diary = await make_diary(db, owner)
    return await make_entry(db, diary, **kwargs)


async def test_add_job_reports_queued(db_session: AsyncSession):
    e = await _entry(db_session)
    await add_job(db_session, e.id)
    assert await ai_statuses(db_session, [e.id]) == {e.id: "queued"}


async def test_started_job_reports_writing(db_session: AsyncSession):
    e = await _entry(db_session)
    job_id = await add_job(db_session, e.id)
    await mark_started(db_session, job_id)
    assert await ai_statuses(db_session, [e.id]) == {e.id: "writing"}


async def test_overlapping_jobs_writing_then_queued_then_none(db_session: AsyncSession):
    e = await _entry(db_session)
    first = await add_job(db_session, e.id)
    second = await add_job(db_session, e.id)
    await mark_started(db_session, first)
    assert await ai_statuses(db_session, [e.id]) == {e.id: "writing"}
    await finish_job(db_session, first)
    assert await ai_statuses(db_session, [e.id]) == {e.id: "queued"}
    await finish_job(db_session, second)
    assert await ai_statuses(db_session, [e.id]) == {}


async def test_finish_and_mark_missing_job_are_noops(db_session: AsyncSession):
    await mark_started(db_session, uuid.uuid4())
    await finish_job(db_session, uuid.uuid4())


async def test_empty_input_returns_empty(db_session: AsyncSession):
    assert await ai_statuses(db_session, []) == {}


async def test_add_job_uses_given_id(db_session: AsyncSession):
    e = await _entry(db_session)
    job_id = uuid.uuid4()
    assert await add_job(db_session, e.id, job_id=job_id) == job_id


async def test_coalesced_job_run_after(db_session: AsyncSession):
    e = await _entry(db_session)
    job_id = await add_job(db_session, e.id, delay_seconds=600)
    await db_session.commit()
    job = (await db_session.execute(select(AiDraftJob).where(AiDraftJob.id == job_id))).scalar_one()
    gap = job.run_after - job.queued_at
    assert abs(gap - timedelta(seconds=600)) < timedelta(seconds=1)


async def test_sweep_deletes_stale_keeps_fresh(db_session: AsyncSession):
    e = await _entry(db_session)
    queued = await add_job(db_session, e.id)
    coalesced = await add_job(db_session, e.id, delay_seconds=600)
    started = await add_job(db_session, e.id)
    await mark_started(db_session, started)
    await db_session.commit()

    swept = await sweep_stale_jobs(db_session, now=datetime.now(UTC) + timedelta(minutes=31))
    await db_session.commit()

    assert swept == 2
    left = (await db_session.execute(select(AiDraftJob.id))).scalars().all()
    assert set(left) == {coalesced}
    assert queued not in left
    assert started not in left


async def test_diary_statuses_filters(db_session: AsyncSession):
    owner = await make_user(db_session)
    diary_a = await make_diary(db_session, owner, name="Diary A")
    diary_b = await make_diary(db_session, owner, name="Diary B")
    draft = await make_entry(db_session, diary_a)
    published = await make_entry(db_session, diary_a, status="published")
    deleted = await make_entry(db_session, diary_a)
    other = await make_entry(db_session, diary_b)
    deleted.deleted_at = datetime.now(UTC)
    for entry in (draft, published, deleted, other):
        await add_job(db_session, entry.id)
    await db_session.commit()

    both = await diary_ai_statuses(db_session, diary_a.id, published_only=False)
    assert both == {draft.id: "queued", published.id: "queued"}
    only_published = await diary_ai_statuses(db_session, diary_a.id, published_only=True)
    assert only_published == {published.id: "queued"}


async def test_job_deleted_with_entry(db_session: AsyncSession):
    e = await _entry(db_session)
    job_id = await add_job(db_session, e.id)
    await db_session.commit()
    await db_session.execute(delete(Entry).where(Entry.id == e.id))
    await db_session.commit()
    rows = (await db_session.execute(select(AiDraftJob).where(AiDraftJob.id == job_id))).all()
    assert rows == []
