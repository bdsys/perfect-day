"""Unit tests: ±90-day scan-window enforcement in calendar_sync.

Tests cover:
  A. Full sync (no sync_token) sends timeMin and timeMax.
  B. Incremental sync (sync_token present) omits timeMin/timeMax, sends syncToken.
  C. Post-filter: events outside the window are dropped before ingest.
  D. _scan_window returns correct bounds.
  E. Post-filter: all-day events inside window are included.
"""

from __future__ import annotations

import datetime as dt_module
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.workers.calendar_sync import _fetch_events, _scan_window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _google_response(items: list[dict], next_sync_token: str = "tok123") -> dict:
    """Minimal Google Calendar events.list JSON response."""
    return {"items": items, "nextSyncToken": next_sync_token}


def _make_httpx_response(body: dict, status_code: int = 200) -> httpx.Response:
    # httpx.Response.raise_for_status() requires a bound request object.
    request = httpx.Request("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events")
    response = httpx.Response(status_code, json=body, request=request)
    return response


# ---------------------------------------------------------------------------
# Test A: full sync sends timeMin and timeMax
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_sync_sends_time_min_and_time_max():
    """_fetch_events with sync_token=None must include timeMin and timeMax.

    Time is frozen so both the code under test and the assertions use the same
    `now`, eliminating flakiness near midnight.
    """
    frozen_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    time_min = frozen_now - timedelta(days=90)
    time_max = frozen_now + timedelta(days=90)

    captured_params: dict = {}

    async def _mock_get(url, *, headers, params, **kwargs):
        captured_params.update(params)
        return _make_httpx_response(_google_response([]))

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.workers.calendar_sync.httpx.AsyncClient", return_value=mock_client),
        patch("app.workers.calendar_sync.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = frozen_now
        mock_dt.fromisoformat = dt_module.datetime.fromisoformat

        events, sync_token = await _fetch_events(
            calendar_id="primary",
            sync_token=None,
            headers={"Authorization": "Bearer x"},
            time_min=time_min,
            time_max=time_max,
        )

    assert "timeMin" in captured_params, "Full sync must send timeMin"
    assert "timeMax" in captured_params, "Full sync must send timeMax"

    # Verify timeMax is exactly 90 days in the future
    time_max_returned = datetime.fromisoformat(captured_params["timeMax"])
    if time_max_returned.tzinfo is None:
        time_max_returned = time_max_returned.replace(tzinfo=UTC)
    assert time_max_returned == time_max, (
        f"timeMax mismatch: got {time_max_returned}, expected {time_max}"
    )

    # Verify timeMin is exactly 90 days in the past
    time_min_returned = datetime.fromisoformat(captured_params["timeMin"])
    if time_min_returned.tzinfo is None:
        time_min_returned = time_min_returned.replace(tzinfo=UTC)
    assert time_min_returned == time_min, (
        f"timeMin mismatch: got {time_min_returned}, expected {time_min}"
    )

    assert "syncToken" not in captured_params, "Full sync must NOT send syncToken"
    assert events == []
    assert sync_token == "tok123"


# ---------------------------------------------------------------------------
# Test B: incremental sync (sync_token present) omits timeMin/timeMax
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_token_path_does_not_send_time_min_max():
    """_fetch_events with a sync_token must send syncToken and omit timeMin/timeMax.

    Time is frozen so both the code under test and the assertions use the same
    `now`, eliminating flakiness near midnight.
    """
    frozen_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    time_min = frozen_now - timedelta(days=90)
    time_max = frozen_now + timedelta(days=90)

    captured_params: dict = {}

    async def _mock_get(url, *, headers, params, **kwargs):
        captured_params.update(params)
        return _make_httpx_response(_google_response([]))

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.workers.calendar_sync.httpx.AsyncClient", return_value=mock_client),
        patch("app.workers.calendar_sync.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = frozen_now
        mock_dt.fromisoformat = dt_module.datetime.fromisoformat

        events, sync_token = await _fetch_events(
            calendar_id="primary",
            sync_token="sometoken",
            headers={"Authorization": "Bearer x"},
            time_min=time_min,
            time_max=time_max,
        )

    assert "syncToken" in captured_params, "Incremental sync must send syncToken"
    assert captured_params["syncToken"] == "sometoken"
    assert "timeMin" not in captured_params, "Incremental sync must NOT send timeMin"
    assert "timeMax" not in captured_params, "Incremental sync must NOT send timeMax"


# ---------------------------------------------------------------------------
# Test C: post-filter drops events outside the scan window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_filter_drops_out_of_window_events():
    """Events from _fetch_events outside ±90 days are dropped before ingest.

    Strategy: mock _fetch_events to return a mix of in-window and out-of-window
    events, mock db_session and ingest_calendar_event.delay, then call
    sync_calendar() and assert only in-window events are enqueued.

    The db mock uses an explicit side_effect list instead of a fragile
    call-count counter so query-order changes don't silently break the test.
    """
    import uuid
    from contextlib import asynccontextmanager

    diary_id = uuid.uuid4()
    now = datetime.now(tz=UTC)

    # One event clearly inside the window (yesterday)
    in_window_event = {
        "id": "evt-in",
        "summary": "In window",
        "status": "confirmed",
        "start": {"dateTime": (now - timedelta(days=1)).isoformat()},
    }
    # One event clearly outside the window (200 days ago)
    out_of_window_event = {
        "id": "evt-out",
        "summary": "Out of window",
        "status": "confirmed",
        "start": {"dateTime": (now - timedelta(days=200)).isoformat()},
    }

    # Build a fake db_session that returns no ScanJob (full sync) and no filters.
    # Use an explicit side_effect list — first call returns ScanJob result,
    # second call returns CalendarFilter result.  No fragile call-count counter.
    fake_session = MagicMock()

    scan_job_result = MagicMock()
    scan_job_result.scalar_one_or_none = MagicMock(return_value=None)  # no existing ScanJob

    filter_result = MagicMock()
    filter_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )  # no filters → "primary"

    fake_session.execute = AsyncMock(side_effect=[scan_job_result, filter_result])
    fake_session.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_db_session():
        yield fake_session

    # Mock _fetch_events to return both events with no sync_token.
    async def _fake_fetch_events(*args, **kwargs):
        return [in_window_event, out_of_window_event], None

    ingest_mock = MagicMock()

    with (
        patch("app.workers.calendar_sync.db_session", _fake_db_session),
        patch("app.workers.calendar_sync._fetch_events", new=_fake_fetch_events),
        patch("app.workers.tasks.ingest_calendar_event") as ingest_task_mock,
    ):
        ingest_task_mock.delay = ingest_mock

        from app.workers.calendar_sync import sync_calendar

        total = await sync_calendar(
            diary_id=diary_id,
            access_token="x",
            diary_timezone="UTC",
            past_days=90,
            future_days=90,
        )

    # Only the in-window event should have been enqueued.
    assert total == 1, f"Expected 1 ingested event, got {total}"
    assert ingest_mock.call_count == 1, (
        f"ingest_calendar_event.delay called {ingest_mock.call_count} times, expected 1"
    )
    call_args = ingest_mock.call_args
    ingested_event = call_args[0][0]
    assert ingested_event["id"] == "evt-in", (
        f"Wrong event ingested: {ingested_event['id']!r}"
    )


# ---------------------------------------------------------------------------
# Test D: _scan_window returns correct bounds
# ---------------------------------------------------------------------------


def test_scan_window_default_bounds():
    """_scan_window() returns (now - 90d, now + 90d) within a 5-second tolerance."""
    before = datetime.now(tz=UTC)
    window_min, window_max = _scan_window()
    after = datetime.now(tz=UTC)

    tolerance = timedelta(seconds=5)

    assert window_min >= before - timedelta(days=90) - tolerance
    assert window_min <= after - timedelta(days=90) + tolerance

    assert window_max >= before + timedelta(days=90) - tolerance
    assert window_max <= after + timedelta(days=90) + tolerance


def test_scan_window_custom_bounds():
    """_scan_window(past_days=30, future_days=7) returns correct custom bounds."""
    before = datetime.now(tz=UTC)
    window_min, window_max = _scan_window(past_days=30, future_days=7)
    after = datetime.now(tz=UTC)

    tolerance = timedelta(seconds=5)

    assert window_min >= before - timedelta(days=30) - tolerance
    assert window_min <= after - timedelta(days=30) + tolerance

    assert window_max >= before + timedelta(days=7) - tolerance
    assert window_max <= after + timedelta(days=7) + tolerance


# ---------------------------------------------------------------------------
# Test E: post-filter includes all-day events inside the scan window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_filter_handles_all_day_events():
    """All-day events (start.date only, no dateTime) inside the window are included.

    All-day events use "date": "YYYY-MM-DD" in the start object. The post-filter
    must parse these correctly and not drop them when they fall within the window.
    """
    import uuid
    from contextlib import asynccontextmanager

    diary_id = uuid.uuid4()
    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    # All-day event today (no dateTime, only date)
    all_day_event = {
        "id": "evt-allday",
        "summary": "All-day event",
        "status": "confirmed",
        "start": {"date": today_str},
    }

    fake_session = MagicMock()

    scan_job_result = MagicMock()
    scan_job_result.scalar_one_or_none = MagicMock(return_value=None)

    filter_result = MagicMock()
    filter_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )

    fake_session.execute = AsyncMock(side_effect=[scan_job_result, filter_result])
    fake_session.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_db_session():
        yield fake_session

    async def _fake_fetch_events(*args, **kwargs):
        return [all_day_event], None

    ingest_mock = MagicMock()

    with (
        patch("app.workers.calendar_sync.db_session", _fake_db_session),
        patch("app.workers.calendar_sync._fetch_events", new=_fake_fetch_events),
        patch("app.workers.tasks.ingest_calendar_event") as ingest_task_mock,
    ):
        ingest_task_mock.delay = ingest_mock

        from app.workers.calendar_sync import sync_calendar

        total = await sync_calendar(
            diary_id=diary_id,
            access_token="x",
            diary_timezone="UTC",
            past_days=90,
            future_days=90,
        )

    assert total == 1, (
        f"Expected all-day event to be included (total=1), got total={total}"
    )
    assert ingest_mock.call_count == 1, (
        f"ingest_calendar_event.delay called {ingest_mock.call_count} times, expected 1"
    )
    ingested_event = ingest_mock.call_args[0][0]
    assert ingested_event["id"] == "evt-allday", (
        f"Wrong event ingested: {ingested_event['id']!r}"
    )
