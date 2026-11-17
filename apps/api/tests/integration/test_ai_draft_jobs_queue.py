"""Every AI draft is queued through enqueue_entry_draft and tracked by a job row (row 38)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiDraftJob, AiDraftSpend, Entry
from app.services.ai_draft_jobs import add_job, ai_statuses
from tests.fixtures.factories import make_diary, make_entry, make_user


@pytest.fixture(autouse=True)
def _wired(client: AsyncClient):
    """Point the worker's db_session() at the test database."""


async def _entry(db: AsyncSession) -> Entry:
    owner = await make_user(db)
    diary = await make_diary(db, owner)
    return await make_entry(db, diary)


async def _jobs(db: AsyncSession, entry_id: uuid.UUID) -> list[AiDraftJob]:
    rows = await db.execute(
        select(AiDraftJob)
        .where(AiDraftJob.entry_id == entry_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


class TestEnqueue:
    async def test_enqueue_inserts_job_and_passes_job_id(self, db_session):
        from app.workers.tasks import enqueue_entry_draft

        e = await _entry(db_session)
        with patch("app.workers.tasks.generate_entry_draft") as task:
            await enqueue_entry_draft(e.id)

        assert await ai_statuses(db_session, [e.id]) == {e.id: "queued"}
        (job,) = await _jobs(db_session, e.id)
        task.delay.assert_called_once_with(str(e.id), job_id=str(job.id))

    async def test_enqueue_regenerate(self, db_session):
        from app.workers.tasks import enqueue_entry_draft

        e = await _entry(db_session)
        with patch("app.workers.tasks.generate_entry_draft") as task:
            await enqueue_entry_draft(e.id, trigger="regenerate")
        task.delay.assert_called_once_with(str(e.id), trigger="regenerate", job_id=ANY)

    async def test_enqueue_coalesced_uses_countdown(self, db_session):
        from app.workers.tasks import enqueue_entry_draft

        e = await _entry(db_session)
        with patch("app.workers.tasks.generate_entry_draft") as task:
            await enqueue_entry_draft(e.id, coalesced=True)
        task.apply_async.assert_called_once_with(
            (str(e.id),), {"coalesced": True, "job_id": ANY}, countdown=600
        )
        task.delay.assert_not_called()
        (job,) = await _jobs(db_session, e.id)
        assert job.run_after - job.queued_at == timedelta(seconds=600)

    async def test_send_failure_removes_job_and_reraises(self, db_session):
        from app.workers.tasks import enqueue_entry_draft

        e = await _entry(db_session)
        with patch("app.workers.tasks.generate_entry_draft") as task:
            task.delay.side_effect = RuntimeError("broker down")
            with pytest.raises(RuntimeError):
                await enqueue_entry_draft(e.id)
        assert await _jobs(db_session, e.id) == []

    async def test_job_insert_failure_still_queues(self, db_session):
        from app.workers.tasks import enqueue_entry_draft

        e = await _entry(db_session)
        with (
            patch("app.workers.tasks.generate_entry_draft") as task,
            patch("app.services.ai_draft_jobs.add_job", AsyncMock(side_effect=RuntimeError("db"))),
        ):
            await enqueue_entry_draft(e.id)
        task.delay.assert_called_once_with(str(e.id), job_id=None)


class TestTaskRuns:
    async def test_task_marks_started_then_clears(self, db_session):
        from app.workers.tasks import _generate_entry_draft

        e = await _entry(db_session)
        job_id = await add_job(db_session, e.id)
        await db_session.commit()
        seen: dict = {}

        async def draft(*args, **kwargs):
            seen.update(await ai_statuses(db_session, [e.id]))

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock(side_effect=draft)),
        ):
            await _generate_entry_draft(str(e.id), job_id=str(job_id))

        assert seen == {e.id: "writing"}
        assert await _jobs(db_session, e.id) == []

    async def test_task_whose_row_was_swept_shows_writing_again(self, db_session):
        from app.workers.tasks import _generate_entry_draft

        e = await _entry(db_session)
        await db_session.commit()
        job_id = uuid.uuid4()  # its row is gone: swept, or the task was redelivered
        seen: dict = {}

        async def draft(*args, **kwargs):
            seen.update(await ai_statuses(db_session, [e.id]))

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock(side_effect=draft)),
        ):
            await _generate_entry_draft(str(e.id), job_id=str(job_id))

        assert seen == {e.id: "writing"}
        assert await _jobs(db_session, e.id) == []

    async def test_task_clears_job_when_draft_raises(self, db_session):
        from app.workers.tasks import _generate_entry_draft

        e = await _entry(db_session)
        job_id = await add_job(db_session, e.id)
        await db_session.commit()

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch(
                "app.workers.llm.generate_draft_for_entry",
                AsyncMock(side_effect=RuntimeError("llm")),
            ),
            pytest.raises(RuntimeError),
        ):
            await _generate_entry_draft(str(e.id), job_id=str(job_id))
        assert await _jobs(db_session, e.id) == []

    async def test_task_without_job_id_writes_nothing(self, db_session):
        from app.workers.tasks import _generate_entry_draft

        e = await _entry(db_session)
        other = await add_job(db_session, e.id)
        await db_session.commit()

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock()),
        ):
            await _generate_entry_draft(str(e.id))

        (job,) = await _jobs(db_session, e.id)
        assert job.id == other
        assert job.started_at is None


class TestQueueRedraftWithoutRedis:
    async def test_queue_redraft_without_redis_creates_job(self, db_session):
        from app.workers.tasks import queue_redraft

        e = await _entry(db_session)
        with (
            patch("app.workers.tasks.generate_entry_draft") as task,
            patch("app.core.dependencies.get_redis", side_effect=RuntimeError("no redis")),
        ):
            await queue_redraft(e.id)
        task.delay.assert_called_once_with(str(e.id), job_id=ANY)
        assert await ai_statuses(db_session, [e.id]) == {e.id: "queued"}


class TestBeatSweep:
    async def test_beat_sweep_clears_stale_jobs(self, db_session):
        from app.workers import beat_tasks

        e = await _entry(db_session)
        old = datetime.now(UTC) - timedelta(minutes=31)
        db_session.add(
            AiDraftJob(id=uuid.uuid4(), entry_id=e.id, queued_at=old, run_after=old, started_at=old)
        )
        await db_session.commit()

        await beat_tasks._release_lost_ai_drafts()
        assert await _jobs(db_session, e.id) == []


class TestBeatSweepFailure:
    async def test_beat_sweep_failure_keeps_quota_release(self, db_session):
        from app.workers import beat_tasks

        owner = await make_user(db_session)
        diary = await make_diary(db_session, owner)
        e = await make_entry(db_session, diary)
        db_session.add(
            AiDraftSpend(
                user_id=owner.id,
                entry_id=e.id,
                month="2026-11",
                key="lost-1",
                sweepable=True,
                created_at=datetime.now(UTC) - timedelta(minutes=31),
            )
        )
        await db_session.commit()

        with patch(
            "app.services.ai_draft_jobs.sweep_stale_jobs",
            AsyncMock(side_effect=RuntimeError("db")),
        ):
            released = await beat_tasks._release_lost_ai_drafts()

        assert released == 1
        keys = (await db_session.execute(select(AiDraftSpend.key))).scalars().all()
        assert "lost-1" not in keys
