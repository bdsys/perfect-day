"""Calendar backfill: pull historical events for a date range."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import structlog

from app.workers.utils import db_session

log = structlog.get_logger()


def _iter_week_chunks(
    from_date: date, to_date: date
) -> tuple[date, date]:
    """Yield (chunk_start, chunk_end_inclusive) windows of up to 7 days.
    Final chunk may be shorter; if from_date == to_date, yields one (d, d).
    """
    if from_date == to_date:
        yield (from_date, to_date)
        return

    cur = from_date
    while cur <= to_date:
        nxt = min(cur + timedelta(days=7), to_date)
        yield (cur, nxt)
        if nxt == to_date:
            break
        cur = nxt


async def run_backfill(
    diary_id: uuid.UUID,
    from_date: date,
    to_date: date,
    access_token: str,
    diary_timezone: str,
) -> tuple[int, int]:
    """Fetch calendar events in [from_date, to_date] and ingest them.

    Returns (events_ingested, entries_created).
    """
    from sqlalchemy import select

    from app.models import DiaryCalendarFilter
    from app.workers.tasks import ingest_calendar_event

    async with db_session() as db:
        filter_result = await db.execute(
            select(DiaryCalendarFilter).where(
                DiaryCalendarFilter.diary_id == diary_id,
                DiaryCalendarFilter.enabled.is_(True),
            )
        )
        filters = filter_result.scalars().all()
        calendar_ids = [f.google_calendar_id for f in filters] if filters else ["primary"]

    headers = {"Authorization": f"Bearer {access_token}"}
    time_min = datetime.combine(from_date, datetime.min.time()).replace(tzinfo=UTC).isoformat()
    time_max = datetime.combine(to_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=UTC).isoformat()

    total_events = 0
    entry_ids: set[str] = set()

    for calendar_id in calendar_ids:
        events = await _fetch_events_range(calendar_id, headers, time_min, time_max)
        total_events += len(events)
        for event in events:
            if event.get("status") == "cancelled":
                continue
            result = ingest_calendar_event.delay(event, str(diary_id), diary_timezone)
            if result:
                entry_ids.add(str(result))

    return total_events, len(entry_ids)


async def _fetch_events_range(
    calendar_id: str,
    headers: dict,
    time_min: str,
    time_max: str,
    max_retries: int = 3,
) -> list[dict]:
    import asyncio

    import httpx

    from app.workers.calendar_sync import CALENDAR_LIST_URL

    url = CALENDAR_LIST_URL.format(calendarId=calendar_id)
    params: dict = {
        "maxResults": 250,
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
    }

    all_events: list[dict] = []

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                while True:
                    resp = await client.get(url, headers=headers, params=params)

                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
                        log.warning("backfill_rate_limited", retry_after=retry_after)
                        await asyncio.sleep(min(retry_after, 16))
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    all_events.extend(data.get("items", []))

                    next_page = data.get("nextPageToken")
                    if next_page:
                        params["pageToken"] = next_page
                    else:
                        break
            break
        except httpx.HTTPStatusError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(4**attempt)

    return all_events
