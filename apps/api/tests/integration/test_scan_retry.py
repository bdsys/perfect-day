"""Failed calendar scans retry sooner with backoff; location tasks retry (TR-2, TR-25)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from celery.exceptions import Retry
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthToken, ScanJob, ScanRun
from tests.integration.test_calendar_connection import _diary_with_google


async def _job(diary) -> ScanJob:
    from app.workers.utils import db_session

    async with db_session() as db:
        job = (await db.execute(select(ScanJob).where(ScanJob.diary_id == diary.id))).scalar_one()
        db.expunge(job)
        return job


async def _make_due(diary) -> None:
    from app.workers.utils import db_session

    async with db_session() as db:
        job = (await db.execute(select(ScanJob).where(ScanJob.diary_id == diary.id))).scalar_one()
        job.next_scan_after = datetime.now(tz=UTC) - timedelta(seconds=1)


async def _scan_again(diary, **kw) -> ScanRun:
    from app.workers.utils import db_session

    await _make_due(diary)
    from app.workers.tasks import _scan_diary

    refresh = kw.get("refresh") or AsyncMock(return_value="tok")
    sync = kw.get("sync") or AsyncMock(return_value=0)
    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", refresh),
        patch("app.workers.calendar_sync.sync_calendar", sync),
    ):
        await _scan_diary(str(diary.id))
    async with db_session() as db:
        return (
            await db.execute(
                select(ScanRun)
                .where(ScanRun.diary_id == diary.id)
                .order_by(ScanRun.started_at.desc())
            )
        ).scalars().first()


def _boom():
    return AsyncMock(side_effect=httpx.ConnectError("down"))


def _delay(job: ScanJob) -> timedelta:
    return job.next_scan_after - job.last_scan_completed_at


class TestScanBackoff:
    async def test_calendar_failure_counts_and_backs_off(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session)
        await _make_due(diary)

        await _scan_again(diary, sync=_boom())
        job = await _job(diary)
        assert job.consecutive_failures == 1
        assert _delay(job) == timedelta(seconds=120)

        await _scan_again(diary, sync=_boom())
        job = await _job(diary)
        assert job.consecutive_failures == 2
        assert _delay(job) == timedelta(seconds=240)

    async def test_unavailable_counts_as_failure(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session)
        await _scan_again(diary, refresh=AsyncMock(return_value=None))
        job = await _job(diary)
        assert job.consecutive_failures == 1
        assert _delay(job) == timedelta(seconds=120)

    async def test_disconnected_calendar_does_not_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A disconnected scan neither adds to nor clears the transient-failure count."""
        from app.workers.utils import db_session as worker_db

        diary = await _diary_with_google(db_session)
        await _scan_again(diary, sync=_boom())
        await _scan_again(diary, sync=_boom())
        assert (await _job(diary)).consecutive_failures == 2

        async with worker_db() as db:
            token = (
                await db.execute(
                    select(OAuthToken).where(OAuthToken.user_id == diary.owner_user_id)
                )
            ).scalar_one()
            token.revoked_at = datetime.now(tz=UTC)

        run = await _scan_again(diary)
        assert [e["error_class"] for e in run.errors] == ["calendar_disconnected"]
        job = await _job(diary)
        assert job.consecutive_failures == 2
        assert _delay(job) == timedelta(minutes=diary.scan_interval_minutes)

    async def test_success_resets_failures(self, client: AsyncClient, db_session: AsyncSession):
        diary = await _diary_with_google(db_session)
        await _scan_again(diary, sync=_boom())
        await _scan_again(diary, sync=_boom())
        assert (await _job(diary)).consecutive_failures == 2

        await _scan_again(diary)
        job = await _job(diary)
        assert job.consecutive_failures == 0
        assert _delay(job) == timedelta(minutes=diary.scan_interval_minutes)

    async def test_error_records_retry_count(self, client: AsyncClient, db_session: AsyncSession):
        diary = await _diary_with_google(db_session)
        run = await _scan_again(diary, sync=_boom())
        assert run.errors[0]["retried_count"] == 0
        run = await _scan_again(diary, sync=_boom())
        assert run.errors[0]["retried_count"] == 1

    def test_backoff_never_exceeds_interval(self):
        from app.workers.tasks import scan_backoff_seconds

        assert scan_backoff_seconds(10, 60) == 3600
        assert scan_backoff_seconds(1, 60) == 120

    def test_scan_diary_declares_no_unused_celery_retries(self):
        from app.workers.tasks import scan_diary

        assert scan_diary.max_retries != 3 or scan_diary.default_retry_delay != 60


class TestLocationTasksRetry:
    def test_refresh_entry_location_retries(self):
        from app.workers.tasks import refresh_entry_location

        with (
            patch(
                "app.workers.tasks._refresh_entry_location",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(refresh_entry_location, "retry", side_effect=Retry()) as retry,
            pytest.raises(Retry),
        ):
            refresh_entry_location.run("00000000-0000-0000-0000-000000000000")
        retry.assert_called_once()

    def test_refresh_diary_locations_retries(self):
        from app.workers.tasks import refresh_diary_locations

        with (
            patch(
                "app.workers.tasks._refresh_diary_locations",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(refresh_diary_locations, "retry", side_effect=Retry()) as retry,
            pytest.raises(Retry),
        ):
            refresh_diary_locations.run("00000000-0000-0000-0000-000000000000")
        retry.assert_called_once()
