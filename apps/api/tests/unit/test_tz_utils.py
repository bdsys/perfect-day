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


class TestGoogleEventTimes:
    """google_event_times: when an event starts and ends, as instants."""

    def test_all_day_event_runs_midnight_to_midnight_in_the_diary_timezone(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        event = {"start": {"date": "2026-06-01"}, "end": {"date": "2026-06-02"}}
        starts_at, ends_at = google_event_times(event, "America/Chicago")
        # Chicago is UTC-5 in June: the day is over at 05:00 UTC, not 00:00 UTC.
        assert starts_at == datetime(2026, 6, 1, 5, 0, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 2, 5, 0, tzinfo=UTC)

    def test_multi_day_all_day_event_ends_after_its_last_day(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        # Google's all-day end is exclusive: 1 June to 4 June covers the 1st to 3rd.
        event = {"start": {"date": "2026-06-01"}, "end": {"date": "2026-06-04"}}
        starts_at, ends_at = google_event_times(event, "UTC")
        assert starts_at == datetime(2026, 6, 1, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 4, tzinfo=UTC)

    def test_all_day_event_without_an_end_lasts_one_day(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        starts_at, ends_at = google_event_times({"start": {"date": "2026-06-01"}}, "UTC")
        assert starts_at == datetime(2026, 6, 1, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 2, tzinfo=UTC)

    def test_timed_event_keeps_its_own_offset(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        event = {
            "start": {"dateTime": "2026-06-01T10:00:00-05:00"},
            "end": {"dateTime": "2026-06-01T11:30:00-05:00"},
        }
        starts_at, ends_at = google_event_times(event, "Europe/London")
        assert starts_at == datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 1, 16, 30, tzinfo=UTC)

    def test_timed_event_without_an_end_ends_when_it_starts(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        event = {"start": {"dateTime": "2026-06-01T10:00:00Z"}, "end": {}}
        starts_at, ends_at = google_event_times(event, "UTC")
        assert starts_at == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    def test_floating_time_is_read_in_the_diary_timezone(self):
        from datetime import UTC, datetime

        from app.workers.tz_utils import google_event_times

        event = {
            "start": {"dateTime": "2026-06-01T10:00:00"},
            "end": {"dateTime": "2026-06-01T11:00:00"},
        }
        starts_at, ends_at = google_event_times(event, "America/Chicago")
        assert starts_at == datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
        assert ends_at == datetime(2026, 6, 1, 16, 0, tzinfo=UTC)

    def test_unparseable_or_missing_start_gives_no_times(self):
        from app.workers.tz_utils import google_event_times

        assert google_event_times({"start": {"dateTime": "not a date"}}, "UTC") == (None, None)
        assert google_event_times({"start": {"date": "2026-13-45"}}, "UTC") == (None, None)
        assert google_event_times({}, "UTC") == (None, None)
