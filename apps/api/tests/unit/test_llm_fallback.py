"""Unit tests: _build_fallback_body deterministic draft generator."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from app.workers.llm import _build_fallback_body

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    *,
    summary: str = "Meeting",
    location: str = "",
    start_dt: str | None = None,
    end_dt: str | None = None,
    all_day: bool = False,
    occurred_at: datetime | None = None,
) -> MagicMock:
    """Build a minimal mock event suitable for _build_fallback_body."""
    ev = MagicMock()
    ev.occurred_at = occurred_at or datetime(2026, 5, 19, 9, 0, tzinfo=UTC)

    payload: dict = {}
    if summary:
        payload["summary"] = summary
    if location:
        payload["location"] = location

    if all_day:
        payload["start"] = {"date": "2026-05-19"}
        payload["end"] = {"date": "2026-05-20"}
    else:
        if start_dt:
            payload["start"] = {"dateTime": start_dt}
        if end_dt:
            payload["end"] = {"dateTime": end_dt}

    ev.payload = payload
    return ev


_DATE = date(2026, 5, 19)


# ---------------------------------------------------------------------------
# Title logic
# ---------------------------------------------------------------------------


class TestBuildFallbackBodyTitle:
    def test_single_event_with_summary_uses_summary_as_title(self):
        events = [_event(summary="Standup")]
        title, _ = _build_fallback_body(events, _DATE)
        assert title == "Standup"

    def test_multiple_events_title_is_n_events_on_date(self):
        events = [_event(summary="Standup"), _event(summary="Lunch"), _event(summary="1:1")]
        title, _ = _build_fallback_body(events, _DATE)
        assert title == "3 events on May 19"

    def test_single_event_no_summary_falls_back_to_n_events(self):
        ev = _event(summary="")
        ev.payload = {}  # no summary key at all
        events = [ev]
        title, _ = _build_fallback_body(events, _DATE)
        assert title == "1 events on May 19"

    def test_two_events_date_in_title(self):
        events = [_event(summary="A"), _event(summary="B")]
        title, _ = _build_fallback_body(events, date(2026, 3, 7))
        assert "2 events on" in title
        assert "March 7" in title


# ---------------------------------------------------------------------------
# Body format — location
# ---------------------------------------------------------------------------


class TestBuildFallbackBodyLocation:
    def test_location_included_with_dash_separator(self):
        events = [_event(
            summary="Standup",
            location="Zoom",
            start_dt="2026-05-19T09:00:00-07:00",
        )]
        _, body = _build_fallback_body(events, _DATE)
        assert "— Zoom" in body

    def test_no_location_omits_separator(self):
        events = [_event(summary="Standup", start_dt="2026-05-19T09:00:00-07:00")]
        _, body = _build_fallback_body(events, _DATE)
        assert "—" not in body

    def test_empty_location_string_omits_separator(self):
        ev = _event(summary="Standup", start_dt="2026-05-19T09:00:00-07:00")
        ev.payload["location"] = ""
        events = [ev]
        _, body = _build_fallback_body(events, _DATE)
        assert "—" not in body


# ---------------------------------------------------------------------------
# Body format — time range
# ---------------------------------------------------------------------------


class TestBuildFallbackBodyTimeRange:
    def test_start_and_end_datetimes_shows_range(self):
        events = [_event(
            summary="Standup",
            start_dt="2026-05-19T09:00:00-07:00",
            end_dt="2026-05-19T09:30:00-07:00",
        )]
        _, body = _build_fallback_body(events, _DATE)
        assert "09:00–09:30" in body

    def test_start_only_shows_single_time(self):
        events = [_event(summary="Reminder", start_dt="2026-05-19T14:15:00-07:00")]
        _, body = _build_fallback_body(events, _DATE)
        assert "14:15" in body
        assert "–" not in body

    def test_all_day_event_shows_all_day_label(self):
        events = [_event(summary="Holiday", all_day=True)]
        _, body = _build_fallback_body(events, _DATE)
        assert "All day" in body
        assert "Holiday" in body

    def test_no_datetime_falls_back_to_occurred_at(self):
        ev = _event(summary="Old event")
        ev.payload = {"summary": "Old event"}  # no start/end keys
        ev.occurred_at = datetime(2026, 5, 19, 11, 45, tzinfo=UTC)
        events = [ev]
        _, body = _build_fallback_body(events, _DATE)
        assert "11:45" in body

    def test_event_no_summary_shows_no_title_placeholder(self):
        ev = _event(summary="")
        ev.payload = {"start": {"dateTime": "2026-05-19T10:00:00+00:00"}}
        events = [ev]
        _, body = _build_fallback_body(events, _DATE)
        assert "(no title)" in body


# ---------------------------------------------------------------------------
# Multi-event body
# ---------------------------------------------------------------------------


class TestBuildFallbackBodyMultipleEvents:
    def test_each_event_is_one_bullet(self):
        events = [
            _event(
                summary="Standup",
                start_dt="2026-05-19T09:00:00-07:00",
                end_dt="2026-05-19T09:30:00-07:00",
            ),
            _event(
                summary="Lunch",
                start_dt="2026-05-19T12:30:00-07:00",
                end_dt="2026-05-19T13:30:00-07:00",
            ),
            _event(
                summary="1:1",
                start_dt="2026-05-19T15:00:00-07:00",
                end_dt="2026-05-19T16:00:00-07:00",
            ),
        ]
        _, body = _build_fallback_body(events, _DATE)
        bullet_lines = [line for line in body.splitlines() if line.startswith("- ")]
        assert len(bullet_lines) == 3

    def test_events_appear_in_order_passed_in(self):
        events = [
            _event(summary="First", start_dt="2026-05-19T09:00:00+00:00"),
            _event(summary="Second", start_dt="2026-05-19T10:00:00+00:00"),
        ]
        _, body = _build_fallback_body(events, _DATE)
        assert body.index("First") < body.index("Second")

    def test_full_bullet_format(self):
        events = [_event(
            summary="Standup",
            location="Zoom",
            start_dt="2026-05-19T09:00:00-07:00",
            end_dt="2026-05-19T09:30:00-07:00",
        )]
        _, body = _build_fallback_body(events, _DATE)
        assert body.strip() == "- 09:00–09:30  **Standup** — Zoom"
