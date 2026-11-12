"""Timezone conversion utilities for scan worker."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytz
from dateutil import parser as dateutil_parser


def google_event_to_entry_date(event: dict, diary_timezone: str) -> tuple[date | None, date | None]:
    """Convert a Google Calendar event dict to (entry_date, entry_end_date) in diary timezone."""
    tz = pytz.timezone(diary_timezone)
    start = event.get("start", {})
    end = event.get("end", {})

    # All-day event (date-only, no time component — floating time)
    if "date" in start:
        start_date = date.fromisoformat(start["date"])
        if "date" in end:
            end_date = date.fromisoformat(end["date"])
            # Google Calendar end is exclusive for all-day; subtract one day
            from datetime import timedelta

            end_date = end_date - timedelta(days=1)
            if end_date == start_date:
                return start_date, None
            return start_date, end_date
        return start_date, None

    # Timed event — convert to diary timezone
    if "dateTime" in start:
        try:
            start_dt = dateutil_parser.isoparse(start["dateTime"])
            if start_dt.tzinfo is None:
                # Floating time — treat as diary timezone
                start_dt = tz.localize(start_dt)
            local_start = start_dt.astimezone(tz)

            end_date_val = None
            if "dateTime" in end:
                end_dt = dateutil_parser.isoparse(end["dateTime"])
                if end_dt.tzinfo is None:
                    end_dt = tz.localize(end_dt)
                local_end = end_dt.astimezone(tz)
                local_end_date = local_end.date()
                if local_end_date != local_start.date():
                    end_date_val = local_end_date

            return local_start.date(), end_date_val
        except Exception:
            return None, None

    return None, None


def google_event_times(
    event: dict, diary_timezone: str
) -> tuple[datetime | None, datetime | None]:
    """When a Google Calendar event starts and ends, as UTC instants.

    An all-day event runs from local midnight to local midnight in the diary's
    timezone, so it has not "ended" until its last day is over there. A timed
    event with no end ends when it starts. Returns (None, None) when the start
    cannot be read.
    """
    tz = pytz.timezone(diary_timezone)
    start = event.get("start") or {}
    end = event.get("end") or {}

    def _local_midnight(day: date) -> datetime:
        return tz.localize(datetime.combine(day, time.min)).astimezone(UTC)

    def _instant(value: str) -> datetime:
        parsed = dateutil_parser.isoparse(value)
        if parsed.tzinfo is None:
            parsed = tz.localize(parsed)  # floating time: read in the diary timezone
        return parsed.astimezone(UTC)

    try:
        if "date" in start:
            start_day = date.fromisoformat(start["date"])
            # Google's all-day end date is exclusive: it is the midnight the event ends at.
            end_day = (
                date.fromisoformat(end["date"]) if "date" in end else start_day + timedelta(days=1)
            )
            return _local_midnight(start_day), _local_midnight(end_day)
        if "dateTime" in start:
            starts_at = _instant(start["dateTime"])
            ends_at = _instant(end["dateTime"]) if "dateTime" in end else starts_at
            return starts_at, ends_at
    except (ValueError, TypeError, OverflowError):
        return None, None
    return None, None
