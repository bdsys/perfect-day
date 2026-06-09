"""Main Celery tasks: scan_diary, ingest_calendar_event, evaluate_rules_for_event, generate_entry_draft."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

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
def scan_diary(self, diary_id: str, past_days: int | None = None, future_days: int | None = None) -> None:
    run_sync(_scan_diary(diary_id, past_days=past_days, future_days=future_days))


async def _scan_diary(diary_id_str: str, past_days: int | None = None, future_days: int | None = None) -> None:
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

    scan_run_id = None
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
                    kwargs = {}
                    if past_days is not None:
                        kwargs["past_days"] = past_days
                    if future_days is not None:
                        kwargs["future_days"] = future_days
                    calendar_events_count = await sync_calendar(
                        diary_id=diary_id,
                        access_token=access_token,
                        diary_timezone=diary.timezone,
                        **kwargs,
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

        # Close scan run
        async with db_session() as db:
            run_result = await db.execute(select(ScanRun).where(ScanRun.id == scan_run_id))
            scan_run_update = run_result.scalar_one()
            job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
            scan_job_update = job_result.scalar_one()

            completed = datetime.now(tz=UTC)
            scan_run_update.completed_at = completed
            scan_run_update.events_calendar = calendar_events_count
            scan_run_update.entries_created = 0  # rules engine sets this in Part 3
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
            # Close any open ScanRun so no zombie rows remain
            if scan_run_id is not None:
                try:
                    run_result = await db.execute(select(ScanRun).where(ScanRun.id == scan_run_id))
                    open_run = run_result.scalar_one_or_none()
                    if open_run and open_run.status == "running":
                        open_run.status = "failed"
                        open_run.completed_at = datetime.now(tz=UTC)
                        open_run.errors = [
                            {
                                "source": "scan_diary",
                                "error_class": type(e).__name__,
                                "message": str(e),
                            }
                        ]
                except Exception:
                    pass  # best-effort: don't mask the original exception

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
    """Ingest one Google Calendar event; store with entry_id=NULL.

    Returns the event's UUID string, or None if the date could not be parsed.
    Rule evaluation is queued separately.
    """
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
        "recurringEventId": event_data.get("recurringEventId"),
        "attendees": _build_attendees(event_data.get("attendees")),
    }

    occurred_at = None
    start = event_data.get("start", {})
    if "dateTime" in start:
        try:
            occurred_at = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        except ValueError:
            pass

    async with db_session() as db:
        existing_result = await db.execute(
            select(Event).where(
                Event.diary_id == diary_id,
                Event.source == source,
                Event.external_id == external_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            existing.payload = payload
            return str(existing.id)

        event = Event(
            diary_id=diary_id,
            entry_id=None,
            source=source,
            external_id=external_id,
            occurred_at=occurred_at,
            payload=payload,
        )
        db.add(event)
        await db.flush()
        event_id = event.id

    # Queue rule evaluation (best-effort: don't roll back a successful ingest
    # just because the broker is briefly down)
    try:
        evaluate_rules_for_event.delay(str(event_id), str(diary_id))
    except Exception:
        log.exception("failed_to_queue_rule_evaluation", event_id=str(event_id), diary_id=str(diary_id))
    return str(event_id)


def _build_attendees(raw: list | None) -> list[dict]:
    """Convert a raw attendees value from Google Calendar into a cleaned list.

    Handles three malformed cases Google Calendar can return:
    - ``None`` / missing key → treated as empty list
    - Non-dict element inside the list → silently skipped
    - Valid dict attendee → normalised and included (only if name or email present)
    """
    attendees = []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        display_name = _strip_injection(str(a.get("displayName", "") or ""))
        email = str(a.get("email", "") or "")
        if display_name or email:
            attendees.append({
                "displayName": display_name,
                "email": email,
                "organizer": bool(a.get("organizer", False)),
                "responseStatus": str(a.get("responseStatus", "") or ""),
            })
    return attendees


def _strip_injection(text: str) -> str:
    """Remove obvious prompt-injection role markers."""
    patterns = [
        r"(?i)^\s*(SYSTEM|ASSISTANT|USER)\s*:",
        r"(?i)```[^`]*(?:SYSTEM|ASSISTANT|USER)[^`]*```",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.MULTILINE)
    return text.strip()




# ---------------------------------------------------------------------------
# evaluate_rules_for_event
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.evaluate_rules_for_event", bind=True, max_retries=3)
def evaluate_rules_for_event(self, event_id: str, diary_id: str) -> None:
    """Evaluate auto-creation rules against a newly ingested event."""
    try:
        run_sync(_evaluate_rules_for_event(event_id, diary_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _evaluate_rules_for_event(event_id: str, diary_id: str) -> None:
    from app.workers.rules import evaluate_event_against_rules
    from app.workers.utils import db_session

    async with db_session() as db:
        await evaluate_event_against_rules(event_id, diary_id, db)


# ---------------------------------------------------------------------------
# apply_rule_backfill
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.tasks.apply_rule_backfill",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def apply_rule_backfill(self, rule_id: str, days: int) -> None:
    """Backfill rule evaluation against past events for the given rule."""
    try:
        run_sync(_apply_rule_backfill(rule_id, days))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _apply_rule_backfill(rule_id_str: str, days: int) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from app.models import AutoCreationRule, Event
    from app.workers.rules import evaluate_event_against_rules
    from app.workers.utils import db_session

    rule_uuid = uuid.UUID(rule_id_str)

    if days <= 0:
        log.warning("apply_rule_backfill_invalid_days", rule_id=rule_id_str, days=days)
        return

    since = datetime.now(UTC) - timedelta(days=days)

    async with db_session() as db:
        # Load the rule to get diary_id
        rule_result = await db.execute(
            select(AutoCreationRule).where(AutoCreationRule.id == rule_uuid)
        )
        rule = rule_result.scalar_one_or_none()
        if rule is None:
            log.warning("apply_rule_backfill_rule_not_found", rule_id=rule_id_str)
            return

        diary_id = rule.diary_id

        # Load all events for this diary in the last `days` days (both attached and unattached)
        events_result = await db.execute(
            select(Event)
            .where(Event.diary_id == diary_id)
            .where(Event.occurred_at >= since)
            .order_by(Event.occurred_at.asc())
            .limit(5000)
        )
        events = list(events_result.scalars())

        # Update last_applied_at
        await db.execute(
            update(AutoCreationRule)
            .where(AutoCreationRule.id == rule_uuid)
            .values(last_applied_at=datetime.now(UTC))
        )
        await db.commit()

    # Process each event through the rules engine (each call gets its own session)
    for event in events:
        try:
            async with db_session() as db:
                await evaluate_event_against_rules(str(event.id), str(diary_id), db)
        except Exception:
            log.exception(
                "apply_rule_backfill_event_failed",
                rule_id=rule_id_str,
                event_id=str(event.id),
            )


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
    from app.workers.enrichments import enrich_entry_weather
    from app.workers.llm import generate_draft_for_entry
    from app.workers.utils import db_session

    entry_uuid = uuid.UUID(entry_id_str)
    try:
        async with db_session() as db:
            await enrich_entry_weather(entry_uuid, db)
    except Exception as exc:  # never block draft on weather failure
        log.warning(
            "enrichment_weather_skipped_due_to_error",
            entry_id=entry_id_str,
            error=str(exc),
        )
    await generate_draft_for_entry(entry_uuid)


# ---------------------------------------------------------------------------
# backfill_diary
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.tasks.backfill_diary",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def backfill_diary(self, backfill_run_id: str) -> None:
    run_sync(_backfill_diary(backfill_run_id))


async def _backfill_diary(backfill_run_id_str: str) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models import BackfillRun, Diary, OAuthToken
    from app.workers.backfill import run_backfill
    from app.workers.token_refresh import ensure_fresh_access_token
    from app.workers.utils import db_session

    backfill_run_id = uuid.UUID(backfill_run_id_str)

    async with db_session() as db:
        result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
        backfill_run = result.scalar_one_or_none()
        if backfill_run is None:
            return

        diary_result = await db.execute(
            select(Diary).where(Diary.id == backfill_run.diary_id, Diary.deleted_at.is_(None))
        )
        diary = diary_result.scalar_one_or_none()
        if diary is None:
            return

        token_result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == diary.owner_user_id,
                OAuthToken.provider == "google",
            )
        )
        oauth_token = token_result.scalar_one_or_none()

        backfill_run.status = "running"
        backfill_run.started_at = datetime.now(tz=UTC)

    if oauth_token is None or oauth_token.revoked_at is not None:
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = "No valid Google OAuth token"
        return

    async with db_session() as db:
        token_result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == diary.owner_user_id,
                OAuthToken.provider == "google",
            )
        )
        oauth_token_fresh = token_result.scalar_one()
        access_token = await ensure_fresh_access_token(oauth_token_fresh, db)

    if not access_token:
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = "Failed to refresh Google access token"
        return

    try:
        events_ingested, entries_created = await run_backfill(
            backfill_run_id=backfill_run_id,
            diary_id=backfill_run.diary_id,
            from_date=backfill_run.from_date,
            to_date=backfill_run.to_date,
            access_token=access_token,
            diary_timezone=diary.timezone,
        )
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "completed"
            run.completed_at = datetime.now(tz=UTC)
            run.events_ingested = events_ingested
            run.entries_created = entries_created
    except Exception as e:
        log.error("backfill_error", backfill_run_id=backfill_run_id_str, error=str(e))
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = str(e)
        raise

