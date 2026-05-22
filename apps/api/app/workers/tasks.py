"""Main Celery tasks: scan_diary, ingest_calendar_event, group_events_into_entries, generate_entry_draft."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

import structlog

from app.workers.celery_app import celery_app
from app.workers.utils import run_sync

log = structlog.get_logger()

CHUNK_NONCE_NFKC = 1_048_576  # 1 MiB per chunk for photo encryption


# ---------------------------------------------------------------------------
# Ping — confirms Celery pipeline works
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    return "pong"


# ---------------------------------------------------------------------------
# scan_diary
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.tasks.scan_diary",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_diary(self, diary_id: str) -> None:
    run_sync(_scan_diary(diary_id))


async def _scan_diary(diary_id_str: str) -> None:
    import asyncio

    from sqlalchemy import select

    from app.core.dependencies import get_redis
    from app.models import Diary, OAuthToken, ScanJob, ScanRun
    from app.workers.calendar_sync import sync_calendar
    from app.workers.token_refresh import ensure_fresh_access_token
    from app.workers.utils import db_session

    diary_id = uuid.UUID(diary_id_str)
    r = get_redis()
    lock_key = f"scan_lock:{diary_id}"

    # Acquire Redis scan lock (30-min TTL + heartbeat)
    acquired = await r.set(lock_key, "1", nx=True, ex=1800)
    if not acquired:
        log.info("scan_skipped_locked", diary_id=diary_id_str)
        return

    heartbeat_task = asyncio.create_task(_heartbeat(r, lock_key))

    try:
        async with db_session() as db:
            result = await db.execute(
                select(Diary).where(Diary.id == diary_id, Diary.deleted_at.is_(None))
            )
            diary = result.scalar_one_or_none()
            if diary is None or not diary.scan_enabled:
                return

            job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
            scan_job = job_result.scalar_one_or_none()
            if scan_job is None:
                return

            now = datetime.now(tz=UTC)
            if scan_job.next_scan_after and scan_job.next_scan_after.replace(tzinfo=UTC) > now:
                return

            # Open scan run
            scan_run = ScanRun(
                diary_id=diary_id,
                triggered_by="beat",
                started_at=now,
                status="running",
            )
            db.add(scan_run)
            await db.flush()
            scan_run_id = scan_run.id
            scan_job.last_scan_started_at = now

            # Get OAuth token
            token_result = await db.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == diary.owner_user_id,
                    OAuthToken.provider == "google",
                )
            )
            oauth_token = token_result.scalar_one_or_none()

        errors = []
        calendar_events_count = 0

        # Calendar sync
        if (
            oauth_token
            and oauth_token.revoked_at is None
            and "calendar.readonly" in (oauth_token.scopes_granted or [])
        ):
            try:
                async with db_session() as db:
                    token_result = await db.execute(
                        select(OAuthToken).where(
                            OAuthToken.user_id == diary.owner_user_id,
                            OAuthToken.provider == "google",
                        )
                    )
                    oauth_token_fresh = token_result.scalar_one()
                    access_token = await ensure_fresh_access_token(oauth_token_fresh, db)

                if access_token:
                    calendar_events_count = await sync_calendar(
                        diary_id=diary_id,
                        access_token=access_token,
                        diary_timezone=diary.timezone,
                    )
            except Exception as e:
                log.error("calendar_sync_error", diary_id=diary_id_str, error=str(e))
                errors.append(
                    {
                        "source": "google_calendar",
                        "error_class": type(e).__name__,
                        "message": str(e),
                        "retried_count": 0,
                    }
                )

        # Group events into entries and queue LLM generation
        new_entry_ids: list[uuid.UUID] = []
        async with db_session() as db:
            new_entry_ids = await group_events_into_entries_async(diary_id, scan_run_id, db)

        for entry_id in new_entry_ids:
            generate_entry_draft.delay(str(entry_id))

        # Close scan run
        async with db_session() as db:
            run_result = await db.execute(select(ScanRun).where(ScanRun.id == scan_run_id))
            scan_run_update = run_result.scalar_one()
            job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
            scan_job_update = job_result.scalar_one()

            completed = datetime.now(tz=UTC)
            scan_run_update.completed_at = completed
            scan_run_update.events_calendar = calendar_events_count
            scan_run_update.entries_created = len(new_entry_ids)
            scan_run_update.errors = errors if errors else None
            scan_run_update.status = "partial" if errors else "success"

            scan_job_update.last_scan_completed_at = completed
            scan_job_update.last_scan_status = scan_run_update.status
            scan_job_update.consecutive_failures = 0
            from datetime import timedelta

            scan_job_update.next_scan_after = completed + timedelta(
                minutes=diary.scan_interval_minutes
            )

    except Exception as e:
        log.error("scan_diary_error", diary_id=diary_id_str, error=str(e))
        async with db_session() as db:
            job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
            scan_job_update = job_result.scalar_one_or_none()
            if scan_job_update:
                scan_job_update.consecutive_failures += 1
                failures = scan_job_update.consecutive_failures
                from datetime import timedelta

                backoff = min(60 * (2**failures), 86400)
                scan_job_update.next_scan_after = datetime.now(tz=UTC) + timedelta(seconds=backoff)
        raise
    finally:
        heartbeat_task.cancel()
        await r.delete(lock_key)


async def _heartbeat(r, lock_key: str) -> None:
    import asyncio

    while True:
        await asyncio.sleep(300)  # renew every 5 minutes
        await r.expire(lock_key, 1800)


# ---------------------------------------------------------------------------
# ingest_calendar_event
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.ingest_calendar_event", bind=True, max_retries=3)
def ingest_calendar_event(self, event_data: dict, diary_id: str, diary_timezone: str) -> str | None:
    """Upsert a single calendar event; return entry_id string or None."""
    return run_sync(_ingest_calendar_event(event_data, uuid.UUID(diary_id), diary_timezone))


async def _ingest_calendar_event(
    event_data: dict, diary_id: uuid.UUID, diary_timezone: str
) -> str | None:
    from sqlalchemy import select

    from app.models import Event
    from app.workers.tz_utils import google_event_to_entry_date
    from app.workers.utils import db_session

    external_id = event_data.get("id", "")
    source = "google_calendar"

    entry_date, entry_end_date = google_event_to_entry_date(event_data, diary_timezone)
    if entry_date is None:
        return None

    # Strip injection markers from user-controlled fields
    for field in ("summary", "description", "location"):
        if field in event_data:
            event_data[field] = _strip_injection(str(event_data.get(field, "")))

    payload = {
        "summary": event_data.get("summary", ""),
        "description": event_data.get("description", ""),
        "location": event_data.get("location", ""),
        "start": event_data.get("start", {}),
        "end": event_data.get("end", {}),
        "status": event_data.get("status", ""),
    }

    occurred_at = None
    start = event_data.get("start", {})
    if "dateTime" in start:
        try:
            occurred_at = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        except ValueError:
            pass

    async with db_session() as db:
        # Check if event already exists
        existing_event = await db.execute(
            select(Event).where(Event.source == source, Event.external_id == external_id)
        )
        existing = existing_event.scalar_one_or_none()
        if existing is not None:
            # Update payload, return existing entry_id
            existing.payload = payload
            return str(existing.entry_id)

        # Find or create entry for this date
        entry_id = await _upsert_entry(db, diary_id, entry_date, entry_end_date, external_id)

        event = Event(
            entry_id=entry_id,
            source=source,
            external_id=external_id,
            occurred_at=occurred_at,
            payload=payload,
        )
        db.add(event)
        return str(entry_id)


def _strip_injection(text: str) -> str:
    """Remove obvious prompt-injection role markers."""
    patterns = [
        r"(?i)^\s*(SYSTEM|ASSISTANT|USER)\s*:",
        r"(?i)```[^`]*(?:SYSTEM|ASSISTANT|USER)[^`]*```",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.MULTILINE)
    return text.strip()


async def _upsert_entry(
    db,
    diary_id: uuid.UUID,
    entry_date: date,
    entry_end_date: date | None,
    external_id: str | None,
) -> uuid.UUID:
    """Find existing draft entry for this date or create a new one."""
    from sqlalchemy import select

    from app.models import Entry

    # Check for multi-day entry match by external_id first
    if external_id and entry_end_date:
        await db.execute(
            select(Entry)
            .where(
                Entry.diary_id == diary_id,
                Entry.status == "draft",
                Entry.deleted_at.is_(None),
            )
            .order_by(Entry.created_at.asc())
        )
        # Look for entry with same external event id already attached
        # (handled by event dedup above)

    # Single-day: find existing draft for this exact date
    result = await db.execute(
        select(Entry)
        .where(
            Entry.diary_id == diary_id,
            Entry.entry_date == entry_date,
            Entry.status == "draft",
            Entry.deleted_at.is_(None),
        )
        .order_by(Entry.created_at.asc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing.id

    # Check if published entry exists for this date — if so, create sibling draft
    # (do not touch published entries)

    entry = Entry(
        diary_id=diary_id,
        entry_date=entry_date,
        entry_end_date=entry_end_date,
        status="draft",
        created_by="auto",
    )
    db.add(entry)
    await db.flush()
    return entry.id


# ---------------------------------------------------------------------------
# group_events_into_entries
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.group_events_into_entries")
def group_events_into_entries(diary_id: str, scan_run_id: str) -> list[str]:
    return run_sync(_group_events_into_entries_task(uuid.UUID(diary_id), uuid.UUID(scan_run_id)))


async def _group_events_into_entries_task(diary_id: uuid.UUID, scan_run_id: uuid.UUID) -> list[str]:
    from app.workers.utils import db_session

    async with db_session() as db:
        ids = await group_events_into_entries_async(diary_id, scan_run_id, db)
    return [str(i) for i in ids]


async def group_events_into_entries_async(
    diary_id: uuid.UUID, scan_run_id: uuid.UUID, db
) -> list[uuid.UUID]:
    """Returns list of entry IDs that need LLM generation."""
    from sqlalchemy import select

    from app.models import Entry, Event

    # Find all draft entries for this diary that have events but no LLM body yet
    result = await db.execute(
        select(Entry).where(
            Entry.diary_id == diary_id,
            Entry.status == "draft",
            Entry.deleted_at.is_(None),
            Entry.body_markdown.is_(None),
        )
    )
    entries_needing_llm = result.scalars().all()

    entries_with_events = []
    for entry in entries_needing_llm:
        event_result = await db.execute(select(Event).where(Event.entry_id == entry.id).limit(1))
        if event_result.scalar_one_or_none() is not None:
            entries_with_events.append(entry.id)

    return entries_with_events


# ---------------------------------------------------------------------------
# generate_entry_draft (LLM)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.tasks.generate_entry_draft",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def generate_entry_draft(self, entry_id: str) -> None:
    run_sync(_generate_entry_draft(entry_id))


async def _generate_entry_draft(entry_id_str: str) -> None:
    from app.workers.llm import generate_draft_for_entry

    await generate_draft_for_entry(uuid.UUID(entry_id_str))
