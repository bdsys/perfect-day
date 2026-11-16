"""Integration tests for backfill worker: chunking, lock, cancellation, counts."""
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AutoCreationRule, BackfillRun, Entry, OAuthToken
from app.workers.backfill import BackfillLocked, _iter_week_chunks, run_backfill

# asyncio_mode = "auto" runs the async tests; the sync ones stay plain.


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
    """15-day range (05-01 to 05-15) -> 3 weekly chunks."""
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

    assert fetch_mock.await_count == 3  # 7 + 7 + 1 days for a 15-day range
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

    user = await make_user(db_session, subscription_tier="plus")
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


# ---------------------------------------------------------------------------
# C-11 / TR-1 — counts tell the truth, chunks don't overlap, a held lock waits
# ---------------------------------------------------------------------------

SOCCER_RULE = {
    "op": "AND",
    "children": [
        {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}
    ],
}
PER_SERIES = {"recurring": "per_series", "multi_day": "per_day"}
LOCKED_MESSAGE = "Another scan was still running. Try the backfill again in a few minutes."


def _event(external_id: str, day: int, summary: str, **extra) -> dict:
    return {
        "id": external_id,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": f"2026-05-{day:02d}T16:00:00Z"},
        "end": {"dateTime": f"2026-05-{day:02d}T17:00:00Z"},
        **extra,
    }


# Two practices from one weekly series, plus something no rule matches.
SERIES_AND_ONE_OTHER = [
    _event("soccer-1", 4, "Soccer practice", recurringEventId="soccer-series"),
    _event("soccer-2", 6, "Soccer practice", recurringEventId="soccer-series"),
    _event("dentist", 5, "Dentist"),
]


async def _diary_with_series_rule(db_session):
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session, subscription_tier="plus")
    diary = await make_diary(db_session, owner=user, timezone="UTC")
    db_session.add(
        AutoCreationRule(
            diary_id=diary.id, name="Soccer", enabled=True, condition=SOCCER_RULE,
            options=PER_SERIES,
        )
    )
    db_session.add(
        OAuthToken(
            user_id=user.id,
            provider="google",
            access_token_ciphertext=b"not-a-real-ciphertext",
            scopes_granted=["https://www.googleapis.com/auth/calendar.readonly"],
        )
    )
    await db_session.commit()
    return diary


async def _pending_run(db_session, diary_id, from_d=date(2026, 5, 4), to_d=date(2026, 5, 6)):
    run = BackfillRun(
        diary_id=diary_id,
        from_date=from_d,
        to_date=to_d,
        sources=["google_calendar"],
        status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    return run


async def _reload(db_session, run_id) -> BackfillRun:
    return (
        await db_session.execute(
            select(BackfillRun)
            .where(BackfillRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def test_chunks_do_not_overlap():
    chunks = list(_iter_week_chunks(date(2026, 1, 1), date(2026, 1, 15)))
    assert chunks == [
        (date(2026, 1, 1), date(2026, 1, 7)),
        (date(2026, 1, 8), date(2026, 1, 14)),
        (date(2026, 1, 15), date(2026, 1, 15)),
    ]


async def test_backfill_counts_events_and_entries(client, db_session):
    from app.workers.tasks import _backfill_diary

    diary = await _diary_with_series_rule(db_session)
    run = await _pending_run(db_session, diary.id)

    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch(
            "app.workers.backfill._fetch_events_range",
            AsyncMock(return_value=SERIES_AND_ONE_OTHER),
        ),
        patch("app.workers.tasks.ingest_calendar_event") as ingest_task,
        patch("app.workers.tasks.evaluate_rules_for_event") as evaluate_task,
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await _backfill_diary(str(run.id))

    run = await _reload(db_session, run.id)
    assert run.status == "completed"
    assert run.events_ingested == 3
    assert run.entries_created == 1
    ingest_task.delay.assert_not_called()  # ingestion is awaited in process
    evaluate_task.delay.assert_not_called()  # so is rule evaluation
    entries = (await db_session.execute(select(Entry).where(Entry.diary_id == diary.id))).all()
    assert len(entries) == 1


async def test_backfill_waits_when_scan_lock_held(client, db_session):
    from app.core.dependencies import get_redis
    from app.workers.tasks import _backfill_diary, _fail_locked_backfill

    diary = await _diary_with_series_rule(db_session)
    run = await _pending_run(db_session, diary.id)
    r = get_redis()
    lock_key = f"scan_lock:{diary.id}"
    await r.set(lock_key, "1", ex=300)

    fetch = AsyncMock(return_value=SERIES_AND_ONE_OTHER)
    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch("app.workers.backfill._fetch_events_range", fetch),
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        try:
            with pytest.raises(BackfillLocked):
                await _backfill_diary(str(run.id))
        finally:
            await r.delete(lock_key)

        locked_out = await _reload(db_session, run.id)
        assert locked_out.status == "pending", "a locked-out run is waiting, not completed"
        assert locked_out.started_at is None
        assert fetch.await_count == 0

        # The retried delivery finds the run pending, so the not-pending guard lets it through.
        await _backfill_diary(str(run.id))

    finished = await _reload(db_session, run.id)
    assert finished.status == "completed"
    assert finished.events_ingested == 3

    # When the retries run out the run is failed with a message the person can act on.
    gave_up = await _pending_run(db_session, diary.id)
    await _fail_locked_backfill(str(gave_up.id))
    gave_up = await _reload(db_session, gave_up.id)
    assert gave_up.status == "failed"
    assert gave_up.error == LOCKED_MESSAGE
    assert gave_up.completed_at is not None


def test_backfill_task_requeues_on_lock_then_fails_after_5_tries():
    """The Celery wrapper re-queues itself every 60 s; after 5 retries it gives up."""
    from app.workers.tasks import backfill_diary

    run_id = "00000000-0000-0000-0000-000000000000"
    locked = AsyncMock(side_effect=BackfillLocked())
    give_up = AsyncMock()
    with (
        patch("app.workers.tasks._backfill_diary", locked),
        patch("app.workers.tasks._fail_locked_backfill", give_up),
        patch.object(backfill_diary, "retry", wraps=backfill_diary.retry) as retry,
    ):
        # apply() runs eagerly and re-runs each retry in place, ignoring the countdown.
        backfill_diary.apply(args=[run_id])

    assert locked.await_count == 6  # the first try and 5 retries
    assert retry.call_count == 6  # the 6th call finds the retries used up
    for call in retry.call_args_list:
        assert call.kwargs == {"countdown": 60, "max_retries": 5}
    give_up.assert_awaited_once_with(run_id)
    assert backfill_diary.max_retries == 5


async def test_backfill_has_no_weather_step(client, db_session):
    diary = await _diary_with_series_rule(db_session)
    run = await _make_run(db_session, diary.id, date(2026, 5, 4), date(2026, 5, 6))

    weather = AsyncMock()
    with (
        patch(
            "app.workers.backfill._fetch_events_range",
            AsyncMock(return_value=SERIES_AND_ONE_OTHER),
        ),
        patch("app.workers.enrichments.enrich_entry_weather", weather),
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        events, entries, _failed = await run_backfill(
            backfill_run_id=run.id,
            diary_id=diary.id,
            from_date=run.from_date,
            to_date=run.to_date,
            access_token="tok",  # noqa: S106
            diary_timezone="UTC",
        )

    assert (events, entries) == (3, 1)
    weather.assert_not_awaited()  # weather comes with the draft (generate_entry_draft)


# ---------------------------------------------------------------------------
# Pre-review rulings: joins are not "created"; one bad event is skipped
# ---------------------------------------------------------------------------


def _chunk_events(by_day: dict[int, dict]):
    """A fake fetch that returns the events whose day falls inside the chunk asked for."""

    async def _fetch(calendar_id, headers, time_min, time_max):
        lo = datetime.fromisoformat(time_min).date()
        hi = datetime.fromisoformat(time_max).date()
        return [ev for day, ev in by_day.items() if lo <= date(2026, 5, day) < hi]

    return _fetch


async def test_two_weeks_of_one_weekly_series_creates_one_entry(client, db_session):
    from app.workers.tasks import _backfill_diary

    diary = await _diary_with_series_rule(db_session)
    run = await _pending_run(db_session, diary.id, date(2026, 5, 4), date(2026, 5, 17))
    weekly = {
        4: _event("soccer-wk1", 4, "Soccer practice", recurringEventId="soccer-series"),
        11: _event("soccer-wk2", 11, "Soccer practice", recurringEventId="soccer-series"),
    }
    fetch = AsyncMock(side_effect=_chunk_events(weekly))

    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch("app.workers.backfill._fetch_events_range", fetch),
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await _backfill_diary(str(run.id))

    assert fetch.await_count == 2  # one chunk per week
    run = await _reload(db_session, run.id)
    assert run.status == "completed"
    assert run.events_ingested == 2
    assert run.entries_created == 1  # week 2 joined week 1's entry


async def test_a_later_backfill_that_only_joins_the_series_creates_nothing(client, db_session):
    from app.workers.tasks import _backfill_diary

    diary = await _diary_with_series_rule(db_session)
    weekly = {
        4: _event("soccer-wk1", 4, "Soccer practice", recurringEventId="soccer-series"),
        18: _event("soccer-wk3", 18, "Soccer practice", recurringEventId="soccer-series"),
    }
    first = await _pending_run(db_session, diary.id, date(2026, 5, 4), date(2026, 5, 10))
    later = await _pending_run(db_session, diary.id, date(2026, 5, 18), date(2026, 5, 24))

    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch("app.workers.backfill._fetch_events_range", side_effect=_chunk_events(weekly)),
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await _backfill_diary(str(first.id))
        await _backfill_diary(str(later.id))

    first = await _reload(db_session, first.id)
    later = await _reload(db_session, later.id)
    assert (first.events_ingested, first.entries_created) == (1, 1)
    assert (later.events_ingested, later.entries_created) == (1, 0)  # joined, not created


async def test_one_event_that_cannot_be_saved_does_not_fail_the_backfill(client, db_session):
    import app.workers.tasks as tasks

    diary = await _diary_with_series_rule(db_session)
    run = await _pending_run(db_session, diary.id)
    real_ingest = tasks._ingest_calendar_event

    async def _ingest(event, *args, **kwargs):
        if event["id"] == "dentist":
            raise RuntimeError("Dentist at 5pm with private details")
        return await real_ingest(event, *args, **kwargs)

    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch(
            "app.workers.backfill._fetch_events_range",
            AsyncMock(return_value=SERIES_AND_ONE_OTHER),
        ),
        patch("app.workers.tasks._ingest_calendar_event", AsyncMock(side_effect=_ingest)),
        patch("app.workers.tasks.generate_entry_draft"),
        patch("asyncio.sleep", new=AsyncMock()),
        patch("app.workers.backfill.log") as log,
    ):
        await tasks._backfill_diary(str(run.id))

    run = await _reload(db_session, run.id)
    assert run.status == "completed"
    assert run.events_ingested == 2
    assert run.entries_created == 1
    assert run.error == "1 event couldn't be saved. Run the backfill again to retry it."
    # Logged with the event id and error type, never the event's text or the error message.
    log.warning.assert_called_once_with(
        "backfill_event_ingest_failed", event_id="dentist", error_class="RuntimeError"
    )


def test_backfill_ingest_failure_message():
    from app.workers.backfill import ingest_failures_message

    assert ingest_failures_message(0) is None
    assert ingest_failures_message(1) == (
        "1 event couldn't be saved. Run the backfill again to retry it."
    )
    assert ingest_failures_message(3) == (
        "3 events couldn't be saved. Run the backfill again to retry them."
    )


def _http_error(status: int):
    import httpx

    url = "https://www.googleapis.com/calendar/v3/calendars/parent@example.com/events?x=1"
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url '{url}'",
        request=request,
        response=httpx.Response(status, request=request),
    )


async def _run_failing_backfill(db_session, exc):
    from app.workers.tasks import _backfill_diary

    diary = await _diary_with_series_rule(db_session)
    run = await _pending_run(db_session, diary.id)
    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", AsyncMock(return_value="tok")),
        patch("app.workers.backfill._fetch_events_range", AsyncMock(side_effect=exc)),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(type(exc)):
            await _backfill_diary(str(run.id))
    return await _reload(db_session, run.id)


async def test_backfill_disconnect_reads_plainly_without_the_url(client, db_session):
    failed = await _run_failing_backfill(db_session, _http_error(401))
    assert failed.status == "failed"
    assert failed.error == (
        "Google Calendar was disconnected during the backfill. "
        "Reconnect it and run the backfill again."
    )
    assert "http" not in failed.error.lower()
    assert "googleapis" not in failed.error


async def test_backfill_other_failure_gives_a_generic_message(client, db_session):
    failed = await _run_failing_backfill(db_session, RuntimeError("secret parent@example.com"))
    assert failed.status == "failed"
    assert failed.error == "The backfill stopped because of a problem. Try again later."


async def test_downgraded_owner_backfill_run_fails(client, db_session):
    """Review Focus 1: a run queued before a downgrade fails; Google is never called."""
    from app.services.tier import BACKFILL_LIMIT_NOTE
    from app.workers.tasks import _backfill_diary
    from tests.fixtures.factories import make_diary, make_user

    user = await make_user(db_session, subscription_tier="free")
    diary = await make_diary(db_session, owner=user)
    run = BackfillRun(
        diary_id=diary.id,
        from_date=date(2026, 5, 1),
        to_date=date(2026, 5, 8),
        sources=["google_calendar"],
        status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    with patch("app.workers.backfill.run_backfill", new=AsyncMock()) as backfill:
        await _backfill_diary(str(run_id))

    backfill.assert_not_awaited()
    db_session.expire_all()
    rows = await db_session.execute(select(BackfillRun).where(BackfillRun.id == run_id))
    run = rows.scalar_one()
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == BACKFILL_LIMIT_NOTE
