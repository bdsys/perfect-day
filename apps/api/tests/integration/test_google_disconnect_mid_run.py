"""A parent who disconnects Google mid-run ends the scan or backfill cleanly (TR-40)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import BackfillRun, OAuthToken, ScanRun
from tests.integration.test_calendar_connection import _diary_with_google


@pytest.fixture(autouse=True)
def wire_worker_db(db_url):
    import app.core.database as db_module

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    original_engine = db_module._engine
    original_factory = db_module._session_factory
    db_module._engine = engine
    db_module._session_factory = factory
    yield
    db_module._engine = original_engine
    db_module._session_factory = original_factory


def _drop_token_on_second_session(owner_id):
    """A db_session() that deletes the owner's Google token as the 2nd session opens."""
    from app.workers import utils

    real = utils.db_session
    opened = 0

    @asynccontextmanager
    async def wrapper():
        nonlocal opened
        opened += 1
        if opened == 2:
            async with real() as db:
                await db.execute(delete(OAuthToken).where(OAuthToken.user_id == owner_id))
        async with real() as db:
            yield db

    return patch("app.workers.utils.db_session", wrapper)


async def test_scan_after_disconnect_mid_run_reports_disconnected(client, db_session):
    from app.workers.tasks import _scan_diary

    diary = await _diary_with_google(db_session)
    await db_session.refresh(diary)
    diary_id = diary.id
    sync = AsyncMock(return_value=0)

    with (
        _drop_token_on_second_session(diary.owner_user_id),
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock()),
        patch("app.workers.calendar_sync.sync_calendar", sync),
    ):
        await _scan_diary(str(diary.id))

    db_session.expire_all()
    rows = await db_session.execute(select(ScanRun).where(ScanRun.diary_id == diary_id))
    run = rows.scalar_one()
    assert run.status == "partial"
    assert [e["error_class"] for e in run.errors] == ["calendar_disconnected"]
    assert sync.await_count == 0


async def test_backfill_after_disconnect_mid_run_fails_cleanly(client, db_session):
    from app.workers.tasks import _backfill_diary

    diary = await _diary_with_google(db_session)
    await db_session.refresh(diary)
    run = BackfillRun(
        diary_id=diary.id,
        from_date=date(2026, 5, 4),
        to_date=date(2026, 5, 6),
        sources=["google_calendar"],
        status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    run_id = str(run.id)

    with (
        _drop_token_on_second_session(diary.owner_user_id),
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock()),
        patch("app.workers.backfill._fetch_events_range", AsyncMock(return_value=[])),
    ):
        await _backfill_diary(run_id)

    db_session.expire_all()
    rows = await db_session.execute(select(BackfillRun).where(BackfillRun.id == uuid.UUID(run_id)))
    done = rows.scalar_one()
    assert done.status == "failed"
    assert done.error == "No valid Google OAuth token"
    assert done.completed_at is not None
    assert done.completed_at <= datetime.now(tz=UTC)
