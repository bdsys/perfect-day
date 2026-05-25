"""Google Calendar incremental sync."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from app.workers.utils import db_session

log = structlog.get_logger()

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events"
DEFAULT_SCAN_PAST_DAYS = 90
DEFAULT_SCAN_FUTURE_DAYS = 90


def _scan_window(
    past_days: int = DEFAULT_SCAN_PAST_DAYS,
    future_days: int = DEFAULT_SCAN_FUTURE_DAYS,
) -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    return (now - timedelta(days=past_days), now + timedelta(days=future_days))


async def sync_calendar(
    diary_id: uuid.UUID,
    access_token: str,
    diary_timezone: str,
    past_days: int = DEFAULT_SCAN_PAST_DAYS,
    future_days: int = DEFAULT_SCAN_FUTURE_DAYS,
) -> int:
    from sqlalchemy import select

    from app.models import DiaryCalendarFilter, ScanJob
    from app.workers.tasks import ingest_calendar_event

    async with db_session() as db:
        job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
        scan_job = job_result.scalar_one_or_none()
        sync_token = scan_job.last_calendar_cursor if scan_job else None

        # Determine calendars to scan (default: primary)
        filter_result = await db.execute(
            select(DiaryCalendarFilter).where(
                DiaryCalendarFilter.diary_id == diary_id,
                DiaryCalendarFilter.enabled.is_(True),
            )
        )
        filters = filter_result.scalars().all()
        calendar_ids = [f.google_calendar_id for f in filters] if filters else ["primary"]

    total_events = 0
    headers = {"Authorization": f"Bearer {access_token}"}

    for calendar_id in calendar_ids:
        time_min, time_max = _scan_window(past_days, future_days)
        raw_events, new_sync_token = await _fetch_events(
            calendar_id, sync_token, headers, time_min=time_min, time_max=time_max
        )

        # Post-filter: drop events outside the scan window (required for delta/sync-token
        # paths where timeMin/timeMax cannot be used).
        filtered_events = []
        for ev in raw_events:
            start = ev.get("start", {})
            dt_str = start.get("dateTime") or start.get("date")
            if dt_str is None:
                continue
            if "T" in dt_str:
                # dateTime: ISO 8601 with optional timezone offset
                occurred_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
            else:
                # date-only (all-day event): treat as midnight UTC on that date
                occurred_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).replace(tzinfo=UTC)
            if time_min <= occurred_at <= time_max:
                filtered_events.append(ev)

        for event in filtered_events:
            if event.get("status") == "cancelled":
                continue
            total_events += 1
            ingest_calendar_event.delay(event, str(diary_id), diary_timezone)

        if new_sync_token:
            async with db_session() as db:
                job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
                job = job_result.scalar_one_or_none()
                if job:
                    job.last_calendar_cursor = new_sync_token

    return total_events


async def _fetch_events(
    calendar_id: str,
    sync_token: str | None,
    headers: dict,
    max_retries: int = 3,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch events with incremental sync. Returns (events, next_sync_token)."""
    url = CALENDAR_LIST_URL.format(calendarId=calendar_id)
    params: dict = {"maxResults": 250, "singleEvents": "true"}

    if sync_token:
        params["syncToken"] = sync_token
    else:
        # Full sync — bound to scan window
        assert time_min is not None and time_max is not None, "_fetch_events requires time_min and time_max"
        params["timeMin"] = time_min.isoformat()
        params["timeMax"] = time_max.isoformat()
        params["orderBy"] = "startTime"

    all_events: list[dict] = []
    next_sync_token = None

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                while True:
                    resp = await client.get(url, headers=headers, params=params)

                    if resp.status_code == 410:
                        # Sync token expired — start fresh next scan
                        log.warning("calendar_sync_token_expired", calendar_id=calendar_id)
                        return all_events, None

                    if resp.status_code == 429:
                        import asyncio

                        retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
                        log.warning("calendar_rate_limited", retry_after=retry_after)
                        await asyncio.sleep(min(retry_after, 16))
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    all_events.extend(data.get("items", []))

                    next_page = data.get("nextPageToken")
                    if next_page:
                        params["pageToken"] = next_page
                        params.pop("syncToken", None)
                    else:
                        next_sync_token = data.get("nextSyncToken")
                        break
            break
        except httpx.HTTPStatusError:
            if attempt == max_retries - 1:
                raise
            import asyncio

            await asyncio.sleep(4**attempt)

    return all_events, next_sync_token
