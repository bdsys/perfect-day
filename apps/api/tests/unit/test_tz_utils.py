"""Unit tests: google_event_to_entry_date for all-day, timed, and floating events."""

from __future__ import annotations

from datetime import date

from app.workers.tz_utils import google_event_to_entry_date


class TestAllDayEvents:
    def test_single_day(self):
        event = {"start": {"date": "2025-03-15"}, "end": {"date": "2025-03-16"}}
        start, end = google_event_to_entry_date(event, "America/New_York")
        assert start == date(2025, 3, 15)
        assert end is None  # end == start → treated as single-day

    def test_multi_day(self):
        # Google all-day end is exclusive, so 3/15–3/17 means 3/15 and 3/16
        event = {"start": {"date": "2025-03-15"}, "end": {"date": "2025-03-17"}}
        start, end = google_event_to_entry_date(event, "America/New_York")
        assert start == date(2025, 3, 15)
        assert end == date(2025, 3, 16)

    def test_no_end_date(self):
        event = {"start": {"date": "2025-06-01"}, "end": {}}
        start, end = google_event_to_entry_date(event, "UTC")
        assert start == date(2025, 6, 1)
        assert end is None


class TestTimedEvents:
    def test_utc_event_converts_to_diary_tz(self):
        # 11 PM UTC on June 1 = June 1 in NY (UTC-4 in summer = 7 PM June 1)
        event = {
            "start": {"dateTime": "2025-06-01T23:00:00Z"},
            "end": {"dateTime": "2025-06-02T00:30:00Z"},
        }
        start, end = google_event_to_entry_date(event, "America/New_York")
        assert start == date(2025, 6, 1)
        assert end is None  # end in NY is still June 1

    def test_midnight_rollover(self):
        # 11 PM UTC June 1 = June 2 in Tokyo (UTC+9 = 8 AM June 2)
        event = {
            "start": {"dateTime": "2025-06-01T23:00:00Z"},
            "end": {"dateTime": "2025-06-02T01:00:00Z"},
        }
        start, end = google_event_to_entry_date(event, "Asia/Tokyo")
        assert start == date(2025, 6, 2)

    def test_multi_day_timed_event(self):
        event = {
            "start": {"dateTime": "2025-06-01T09:00:00-04:00"},
            "end": {"dateTime": "2025-06-03T09:00:00-04:00"},
        }
        start, end = google_event_to_entry_date(event, "America/New_York")
        assert start == date(2025, 6, 1)
        assert end == date(2025, 6, 3)

    def test_malformed_datetime_returns_none(self):
        event = {"start": {"dateTime": "not-a-date"}, "end": {"dateTime": "also-not"}}
        start, end = google_event_to_entry_date(event, "UTC")
        assert start is None
        assert end is None


class TestFloatingTimeEvents:
    def test_floating_time_localized_to_diary_tz(self):
        # No tzinfo → treated as diary timezone local time
        event = {
            "start": {"dateTime": "2025-07-04T10:00:00"},
            "end": {"dateTime": "2025-07-04T11:00:00"},
        }
        start, end = google_event_to_entry_date(event, "America/Chicago")
        assert start == date(2025, 7, 4)
        assert end is None

    def test_empty_event_returns_none(self):
        start, end = google_event_to_entry_date({}, "UTC")
        assert start is None
        assert end is None
