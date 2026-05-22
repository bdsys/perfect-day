"""Timezone conversion utilities for scan worker."""

from __future__ import annotations

from datetime import date

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
