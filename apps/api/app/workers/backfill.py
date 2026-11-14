"""Calendar backfill: pull historical events for a date range."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import structlog

from app.workers.utils import db_session

log = structlog.get_logger()


class BackfillLocked(Exception):
    """Another scan or backfill holds this diary's scan lock; try again later."""


def _iter_week_chunks(
    from_date: date, to_date: date
) -> Iterator[tuple[date, date]]:
    """Yield (chunk_start, chunk_end_inclusive) windows of up to 7 days.

    Each chunk starts the day after the previous one ends, so no day is fetched
    twice. The final chunk may be shorter; if from_date == to_date, yields (d, d).
    """
    cur = from_date
    while cur <= to_date:
        nxt = min(cur + timedelta(days=6), to_date)
        yield (cur, nxt)
        cur = nxt + timedelta(days=1)


async def run_backfill(
    backfill_run_id: uuid.UUID,
    diary_id: uuid.UUID,
    from_date: date,
    to_date: date,
    access_token: str,
    diary_timezone: str,
) -> tuple[int, int]:
    """Weekly-chunked backfill with scan_lock, heartbeat, and cancellation.

    Acquires scan_lock:{diary_id} (30-min TTL + 5-min heartbeat).
    Re-reads BackfillRun.status at each chunk boundary; breaks if cancelled.
    Each event is ingested in process, then the chunk's ended events have their
    rules run in process, so the counts are what actually happened.

    Returns (events ingested, distinct entries those events now belong to).
    Raises BackfillLocked if the lock is already held.
    """
    import asyncio

    from sqlalchemy import select

    import app.workers.tasks as _tasks
    from app.core.dependencies import get_redis
    from app.models import BackfillRun, DiaryCalendarFilter

    r = get_redis()
    lock_key = f"scan_lock:{diary_id}"
    acquired = await r.set(lock_key, "1", nx=True, ex=1800)
    if not acquired:
        log.info("backfill_locked", diary_id=str(diary_id))
        raise BackfillLocked(str(diary_id))

    heartbeat_task = asyncio.create_task(_tasks._heartbeat(r, lock_key))
    try:
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
        total_events = 0
        entry_ids: set[uuid.UUID] = set()

        for chunk_start, chunk_end in _iter_week_chunks(from_date, to_date):
            async with db_session() as db:
                cur_status = (
                    await db.execute(
                        select(BackfillRun.status).where(BackfillRun.id == backfill_run_id)
                    )
                ).scalar_one_or_none()
            if cur_status == "cancelled":
                log.info("backfill_cancelled", backfill_run_id=str(backfill_run_id))
                break

            time_min = (
                datetime.combine(chunk_start, datetime.min.time())
                .replace(tzinfo=UTC)
                .isoformat()
            )
            time_max = (
                datetime.combine(chunk_end + timedelta(days=1), datetime.min.time())
                .replace(tzinfo=UTC)
                .isoformat()
            )

            for calendar_id in calendar_ids:
                events = await _fetch_events_range(calendar_id, headers, time_min, time_max)
                for event in events:
                    if event.get("status") == "cancelled":
                        continue
                    # Rules are run below, for the whole chunk, not queued per event.
                    event_id = await _tasks._ingest_calendar_event(
                        event, diary_id, diary_timezone, queue_evaluation=False
                    )
                    if event_id is not None:
                        total_events += 1

            # TODO(item-15): call group_events_into_entries for this chunk once it exists.

            # Weather is not fetched here: generate_entry_draft adds it to each draft.
            entry_ids |= await _tasks.evaluate_due_events(diary_id)

            await asyncio.sleep(2)

        return total_events, len(entry_ids)
    finally:
        heartbeat_task.cancel()
        await r.delete(lock_key)


async def _fetch_events_range(
    calendar_id: str,
    headers: dict,
    time_min: str,
    time_max: str,
    max_retries: int = 3,
) -> list[dict]:
    import asyncio

    import httpx

    from app.workers.calendar_sync import (
        CALENDAR_LIST_URL,
        MAX_RATE_LIMIT_WAITS,
        _retry_after_seconds,
    )

    url = CALENDAR_LIST_URL.format(calendarId=calendar_id)
    params: dict = {
        "maxResults": 250,
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
    }

    all_events: list[dict] = []
    rate_limit_waits = 0

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                while True:
                    resp = await client.get(url, headers=headers, params=params)

                    if resp.status_code == 429 and rate_limit_waits < MAX_RATE_LIMIT_WAITS:
                        rate_limit_waits += 1
                        retry_after = _retry_after_seconds(resp, 2**attempt)
                        log.warning("backfill_rate_limited", retry_after=retry_after)
                        await asyncio.sleep(retry_after)
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
