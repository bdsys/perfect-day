"""Scan options' past/future days fetch that window without touching the sync token (TR-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ScanJob


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


async def _diary_with_token(db_session):
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, user)
    job = (
        await db_session.execute(select(ScanJob).where(ScanJob.diary_id == diary.id))
    ).scalar_one()
    job.last_calendar_cursor = "tok-1"
    await db_session.commit()
    return diary


async def _cursor(db_session, diary_id):
    job = (
        await db_session.execute(
            select(ScanJob)
            .where(ScanJob.diary_id == diary_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return job.last_calendar_cursor


async def test_window_ignores_and_keeps_sync_token(client, db_session):
    from app.workers.calendar_sync import sync_calendar

    diary = await _diary_with_token(db_session)
    fetch = AsyncMock(return_value=([], "tok-2"))
    with patch("app.workers.calendar_sync._fetch_events", fetch):
        await sync_calendar(diary.id, "at", "UTC", past_days=30)

    args, kwargs = fetch.call_args
    assert args[1] is None
    expected = datetime.now(tz=UTC) - timedelta(days=30)
    assert abs(kwargs["time_min"] - expected) < timedelta(minutes=1)
    assert await _cursor(db_session, diary.id) == "tok-1"


async def test_no_window_uses_sync_token(client, db_session):
    from app.workers.calendar_sync import sync_calendar

    diary = await _diary_with_token(db_session)
    fetch = AsyncMock(return_value=([], "tok-2"))
    with patch("app.workers.calendar_sync._fetch_events", fetch):
        await sync_calendar(diary.id, "at", "UTC")

    assert fetch.call_args.args[1] == "tok-1"
    assert await _cursor(db_session, diary.id) == "tok-2"
