"""Integration tests for backfill worker: chunking, lock, cancellation."""
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import BackfillRun, Enrichment
from app.workers.backfill import run_backfill

pytestmark = pytest.mark.asyncio


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


async def _make_run(db_session, diary_id, from_d, to_d):
    run = BackfillRun(
        diary_id=diary_id,
        from_date=from_d,
        to_date=to_d,
        sources=["google_calendar"],
        status="running",
        started_at=datetime.now(tz=UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


async def test_run_backfill_fetches_each_weekly_chunk(client, db_session):
    """15-day range (05-01 to 05-15) -> 2 weekly chunks."""
    from tests.fixtures.factories import make_diary, make_user
    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    run = await _make_run(db_session, diary.id, date(2026, 5, 1), date(2026, 5, 15))

    fetch_mock = AsyncMock(return_value=[])
    with patch("app.workers.backfill._fetch_events_range", fetch_mock), \
         patch("app.workers.tasks.ingest_calendar_event"), \
         patch("asyncio.sleep", new=AsyncMock()):
        await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    assert fetch_mock.await_count == 2  # 2 chunks for 15-day range
    # Verify chunk time ranges are ordered (start times increase)
    call_args = [c.args for c in fetch_mock.await_args_list]
    starts = [args[2] for args in call_args]  # time_min is 3rd positional arg
    assert starts == sorted(starts)


async def test_run_backfill_acquires_and_releases_lock(client, db_session):
    from app.core.dependencies import get_redis
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    run = await _make_run(db_session, diary.id, date(2026, 5, 1), date(2026, 5, 8))

    r = get_redis()
    lock_key = f"scan_lock:{diary.id}"
    assert not await r.exists(lock_key)

    with patch("app.workers.backfill._fetch_events_range", AsyncMock(return_value=[])), \
         patch("app.workers.tasks.ingest_calendar_event"), \
         patch("asyncio.sleep", new=AsyncMock()):
        await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    # Lock must be released in finally
    assert not await r.exists(lock_key)


async def test_run_backfill_skips_when_lock_held(client, db_session):
    from app.core.dependencies import get_redis
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    run = await _make_run(db_session, diary.id, date(2026, 5, 1), date(2026, 5, 8))

    r = get_redis()
    lock_key = f"scan_lock:{diary.id}"
    await r.set(lock_key, "1", ex=300)

    fetch_mock = AsyncMock(return_value=[])
    try:
        result = await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )
    finally:
        await r.delete(lock_key)

    assert fetch_mock.await_count == 0
    assert result == (0, 0)


async def test_run_backfill_breaks_on_cancellation(client, db_session):
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    # 22-day range -> 4 chunks. Cancel after first fetch.
    run = await _make_run(db_session, diary.id, date(2026, 5, 1), date(2026, 5, 22))

    call_count = {"n": 0}

    async def fake_fetch(*a, **kw):
        if call_count["n"] == 0:
            # Set the run to cancelled in DB after first chunk fetch
            from app.workers.utils import db_session as worker_db_session
            async with worker_db_session() as s:
                res = await s.execute(select(BackfillRun).where(BackfillRun.id == run.id))
                r = res.scalar_one()
                r.status = "cancelled"
        call_count["n"] += 1
        return []

    with patch("app.workers.backfill._fetch_events_range", side_effect=fake_fetch), \
         patch("app.workers.tasks.ingest_calendar_event"), \
         patch("asyncio.sleep", new=AsyncMock()):
        await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    # Cancellation check fires at chunk boundary, so worker processed 1 chunk
    # and the cancellation is detected before the 2nd chunk starts
    assert call_count["n"] == 1


async def test_post_then_delete_then_worker_exits_cleanly(client, db_session):
    """If run is already cancelled when worker starts, no chunks are fetched."""
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    run = await _make_run(db_session, diary.id, date(2026, 5, 1), date(2026, 5, 22))

    # Simulate: DELETE endpoint fired before worker even polled first chunk
    run.status = "cancelled"
    await db_session.commit()

    fetch_mock = AsyncMock(return_value=[])
    with patch("app.workers.backfill._fetch_events_range", fetch_mock), \
         patch("app.workers.tasks.ingest_calendar_event"), \
         patch("asyncio.sleep", new=AsyncMock()):
        await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    assert fetch_mock.await_count == 0


async def test_backfill_writes_weather_per_entry(client, db_session, make_user, make_diary_for_user):
    """After each chunk, run_backfill should call enrich_entry_weather for each created entry,
    resulting in Enrichment rows with kind='weather' and source='open_meteo' in the DB."""
    from tests.fixtures.factories import make_entry

    # 1. Create a user and a diary with known lat/lon (Pittsburgh, PA).
    user = await make_user()
    diary = await make_diary_for_user(user, lat=40.4406, lon=-79.9959)

    # 2. Create a real Entry in the DB so enrich_entry_weather can load it.
    entry = await make_entry(db_session, diary, entry_date=date(2026, 5, 5))

    # 3. Create a BackfillRun (single-day range -> 1 chunk).
    run = await _make_run(db_session, diary.id, date(2026, 5, 5), date(2026, 5, 5))

    # 4. Fake calendar event (one non-cancelled event).
    fake_events = [
        {
            "id": "ev1",
            "summary": "Morning walk",
            "status": "confirmed",
            "start": {"dateTime": "2026-05-05T08:00:00Z"},
            "end": {"dateTime": "2026-05-05T09:00:00Z"},
        },
    ]

    # 5. ingest_calendar_event.delay returns the entry ID (str), mimicking
    #    the task returning an entry UUID when it creates/updates an entry.
    task_mock = MagicMock()
    task_mock.delay.return_value = str(entry.id)

    # 6. open_meteo.fetch_daily returns a minimal weather payload.
    fake_weather = {"temperature_2m_max": 22.0, "temperature_2m_min": 14.0}

    with (
        patch("app.workers.backfill._fetch_events_range", AsyncMock(return_value=fake_events)),
        patch("app.workers.tasks.ingest_calendar_event", task_mock),
        patch("app.workers.open_meteo.fetch_daily", AsyncMock(return_value=fake_weather)),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    # 7. Assert Enrichment rows were written.
    result = await db_session.execute(
        select(Enrichment).where(
            Enrichment.entry_id == entry.id,
            Enrichment.kind == "weather",
            Enrichment.source == "open_meteo",
        )
    )
    rows = result.scalars().all()
    assert len(rows) >= 1, (
        f"Expected at least 1 Enrichment row for entry {entry.id}, found {len(rows)}"
    )
