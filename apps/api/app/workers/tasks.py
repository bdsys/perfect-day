"""Main Celery tasks: scan_diary, ingest_calendar_event, evaluate_rules_for_event, generate_entry_draft."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog

from app.workers.celery_app import celery_app
from app.workers.utils import run_sync

log = structlog.get_logger()

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
)
def scan_diary(
    self,
    diary_id: str,
    past_days: int | None = None,
    future_days: int | None = None,
    triggered_by: str = "beat",
) -> None:
    run_sync(
        _scan_diary(
            diary_id, past_days=past_days, future_days=future_days, triggered_by=triggered_by
        )
    )


def scan_backoff_seconds(failures: int, interval_minutes: int) -> int:
    """Wait after a failed calendar scan: doubles per failure, never beyond the scan interval."""
    return min(60 * 2**failures, interval_minutes * 60)


_CALENDAR_DISCONNECTED = {
    "source": "google_calendar",
    "error_class": "calendar_disconnected",
    "message": "Google Calendar is disconnected. "
    "Reconnect it to keep your diary up to date.",
    "retried_count": 0,
}
_CALENDAR_UNAVAILABLE = {
    "source": "google_calendar",
    "error_class": "calendar_unavailable",
    "message": "Couldn't reach Google Calendar. We'll try again at the next scan.",
    "retried_count": 0,
}


async def _scan_diary(
    diary_id_str: str,
    past_days: int | None = None,
    future_days: int | None = None,
    triggered_by: str = "beat",
) -> None:
    import asyncio

    from sqlalchemy import select

    from app.core.dependencies import get_redis
    from app.models import Diary, OAuthToken, ScanJob, ScanRun, User
    from app.services.calendar_connection import calendar_state
    from app.services.read_only import is_diary_read_only
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

            if await is_diary_read_only(db, diary):
                log.info("diary_read_only_skip", diary_id=diary_id_str, where="scan")
                return

            owner_deleted_at = (
                await db.execute(select(User.deleted_at).where(User.id == diary.owner_user_id))
            ).scalar_one_or_none()
            if owner_deleted_at is not None:
                log.info("scan_skipped_owner_deleted", diary_id=diary_id_str)
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
                triggered_by=triggered_by,
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

        # Calendar sync. An owner who never connected Google has nothing to
        # scan and is not warned; a connection that exists but cannot be read
        # is always reported, never skipped silently (C-18).
        connection = calendar_state(oauth_token)
        if connection == "lost":
            errors.append(dict(_CALENDAR_DISCONNECTED))
        elif connection == "connected":
            try:
                async with db_session() as db:
                    token_result = await db.execute(
                        select(OAuthToken).where(
                            OAuthToken.user_id == diary.owner_user_id,
                            OAuthToken.provider == "google",
                        )
                    )
                    oauth_token_fresh = token_result.scalar_one_or_none()
                    if oauth_token_fresh is None:
                        # Disconnected while the scan ran.
                        access_token = None
                        grant_gone = True
                    else:
                        access_token = await ensure_fresh_access_token(oauth_token_fresh, db)
                        grant_gone = oauth_token_fresh.revoked_at is not None

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
                elif grant_gone:
                    errors.append(dict(_CALENDAR_DISCONNECTED))
                else:
                    errors.append(dict(_CALENDAR_UNAVAILABLE))
            except Exception as e:
                log.error("calendar_sync_error", diary_id=diary_id_str, error=str(e))
                errors.append(
                    {
                        "source": "google_calendar",
                        "error_class": type(e).__name__,
                        "message": "Couldn't read Google Calendar. We'll try again at the next scan.",
                        "detail": str(e),
                        "retried_count": 0,
                    }
                )

        # Events stored while still in the future get their rules run once
        # they are over. Independent of the calendar sync succeeding.
        entry_ids: set[uuid.UUID] = set()
        try:
            entry_ids = await evaluate_due_events(diary_id)
        except Exception as e:
            log.error("evaluate_due_events_error", diary_id=diary_id_str, error=str(e))
            errors.append(
                {
                    "source": "rules",
                    "error_class": type(e).__name__,
                    "message": "Couldn't check your rules against new events. "
                    "We'll try again at the next scan.",
                    "detail": str(e),
                    "retried_count": 0,
                }
            )

        # Only a calendar error that may clear on its own is a failure to retry
        # sooner; a disconnected calendar waits for the person (the normal
        # interval) and a rules error is not about the calendar.
        calendar_errors = [e for e in errors if e["source"] == "google_calendar"]
        transient = [e for e in calendar_errors if e["error_class"] != "calendar_disconnected"]

        # Close scan run
        async with db_session() as db:
            run_result = await db.execute(select(ScanRun).where(ScanRun.id == scan_run_id))
            scan_run_update = run_result.scalar_one()
            job_result = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary_id))
            scan_job_update = job_result.scalar_one()

            completed = datetime.now(tz=UTC)
            scan_run_update.completed_at = completed
            scan_run_update.events_calendar = calendar_events_count
            scan_run_update.entries_created = len(entry_ids)
            scan_run_update.errors = errors if errors else None
            scan_run_update.status = "partial" if errors else "success"

            scan_job_update.last_scan_completed_at = completed
            scan_job_update.last_scan_status = scan_run_update.status
            from datetime import timedelta

            failures_before = scan_job_update.consecutive_failures
            if transient:
                for err in transient:
                    err["retried_count"] = failures_before
                scan_run_update.errors = errors
                scan_job_update.consecutive_failures = failures_before + 1
                scan_job_update.next_scan_after = completed + timedelta(
                    seconds=scan_backoff_seconds(
                        scan_job_update.consecutive_failures, diary.scan_interval_minutes
                    )
                )
            else:
                if not calendar_errors:
                    scan_job_update.consecutive_failures = 0
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
                                "message": "The scan stopped unexpectedly. "
                                "It will run again later.",
                                "detail": str(e),
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
    """Upsert a single calendar event; return its event id string, or None."""
    return run_sync(_ingest_calendar_event(event_data, uuid.UUID(diary_id), diary_timezone))


async def _ingest_calendar_event(
    event_data: dict,
    diary_id: uuid.UUID,
    diary_timezone: str,
    *,
    queue_evaluation: bool = True,
) -> str | None:
    """Ingest one Google Calendar event; store with entry_id=NULL.

    Returns the event's UUID string, or None if the date could not be parsed.
    A new event that is already over has its rule evaluation queued, unless
    *queue_evaluation* is False: backfill runs evaluate_due_events itself, in
    process, so it can count the entries.
    """
    from sqlalchemy import select

    from app.models import Event
    from app.workers.tz_utils import google_event_times, google_event_to_entry_date
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

    # All-day events get times too (local midnight to local midnight): without
    # them "has it ended" cannot be answered.
    occurred_at, ends_at = google_event_times(event_data, diary_timezone)

    async with db_session() as db:
        existing_result = await db.execute(
            select(Event).where(
                Event.diary_id == diary_id,
                Event.source == source,
                Event.external_id == external_id,
            )
        )
        event = existing_result.scalar_one_or_none()
        is_new = event is None
        entry_to_redraft: uuid.UUID | None = None
        if event is not None:
            event.payload = payload
            event.occurred_at = occurred_at
            event.ends_at = ends_at
            if not _has_ended(ends_at):
                # Moved to a time that has not passed. A draft only the AI has
                # written was about something that has not happened yet, so it
                # goes; anything a person edited or published stays as it is.
                entry_to_redraft = await _release_from_untouched_draft(db, event)
                if event.entry_id is None:
                    event.rules_evaluated_at = None  # evaluate after the new end
        else:
            event = Event(
                diary_id=diary_id,
                entry_id=None,
                source=source,
                external_id=external_id,
                occurred_at=occurred_at,
                ends_at=ends_at,
                payload=payload,
            )
            db.add(event)
            await db.flush()
        event_id = event.id
        # Rules run once, and only after the event is over. A new event that
        # is already over is evaluated now; anything else waits for the sweep
        # in the scan that follows its end (evaluate_due_events).
        due = queue_evaluation and is_new and _has_ended(ends_at)

    if entry_to_redraft is not None:
        try:
            generate_entry_draft.delay(str(entry_to_redraft))
        except Exception:
            log.exception("reschedule_redraft_queue_failed", entry_id=str(entry_to_redraft))

    if due:
        # Best-effort: a broker hiccup must not undo a successful ingest. The
        # next scan's sweep queues it again.
        try:
            evaluate_rules_for_event.delay(str(event_id), str(diary_id))
        except Exception:
            log.exception(
                "failed_to_queue_rule_evaluation", event_id=str(event_id), diary_id=str(diary_id)
            )
    return str(event_id)


# ---------------------------------------------------------------------------
# cancel_calendar_event
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.cancel_calendar_event", bind=True, max_retries=3)
def cancel_calendar_event(self, external_id: str, diary_id: str) -> None:
    """Apply a cancellation reported by Google Calendar."""
    try:
        run_sync(_cancel_calendar_event(external_id, diary_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _cancel_calendar_event(external_id: str, diary_id: str) -> None:
    """Remove a cancelled event; its entry follows only if nobody has touched it."""
    from sqlalchemy import select

    from app.models import Event
    from app.workers.event_changes import release_event_from_entry
    from app.workers.utils import db_session

    async with db_session() as db:
        event = (
            await db.execute(
                select(Event)
                .where(
                    Event.diary_id == uuid.UUID(diary_id),
                    Event.source == "google_calendar",
                    Event.external_id == external_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if event is None:
            return  # never stored, or already removed
        entry_to_redraft = await release_event_from_entry(db, event)
        await db.delete(event)

    # The session has committed; only now can a worker see the change.
    if entry_to_redraft is not None:
        try:
            generate_entry_draft.delay(str(entry_to_redraft))
        except Exception:
            log.exception("cancel_event_redraft_queue_failed", entry_id=str(entry_to_redraft))


async def _release_from_untouched_draft(db, event) -> uuid.UUID | None:
    """Detach *event* from its entry if that entry is an untouched rule-created draft.

    Returns the id of an entry that needs redrafting, as release_event_from_entry does.
    """
    from sqlalchemy import select

    from app.models import Entry
    from app.workers.event_changes import is_untouched_rule_draft, release_event_from_entry

    if event.entry_id is None:
        return None
    entry = (
        await db.execute(select(Entry).where(Entry.id == event.entry_id))
    ).scalar_one_or_none()
    if entry is None or not is_untouched_rule_draft(entry):
        return None
    return await release_event_from_entry(db, event)


def _has_ended(ends_at: datetime | None) -> bool:
    """An event with no known end counts as over."""
    return ends_at is None or ends_at <= datetime.now(tz=UTC)


async def evaluate_due_events(diary_id: uuid.UUID) -> set[uuid.UUID]:
    """Run the rules, in process, for stored events that are over and not yet evaluated.

    Returns the distinct ids of the entries this sweep created. An event that
    joined an entry which already existed (a recurring series, a spanning day)
    is not counted. An event whose evaluation fails is logged and skipped; its
    rules_evaluated_at stays NULL, so the next scan tries it again.
    """
    from sqlalchemy import func, or_, select

    from app.models import Entry, Event
    from app.workers.utils import db_session

    async with db_session() as db:
        # The database clock, as Entry.created_at uses: this transaction starts
        # before any evaluation below, so every entry they create is at or after it.
        sweep_started = (await db.execute(select(func.now()))).scalar_one()
        result = await db.execute(
            select(Event.id).where(
                Event.diary_id == diary_id,
                Event.entry_id.is_(None),
                Event.rules_evaluated_at.is_(None),
                or_(Event.ends_at.is_(None), Event.ends_at <= func.now()),
            )
        )
        event_ids = [row[0] for row in result.fetchall()]

    if not event_ids:
        return set()

    evaluated = []
    for event_id in event_ids:
        try:
            await _evaluate_rules_for_event(str(event_id), str(diary_id))
        except Exception as exc:
            # The id and error type only: the message or traceback can carry
            # calendar details, as in the backfill.
            log.warning(
                "evaluate_due_event_failed",
                event_id=str(event_id),
                error_class=type(exc).__name__,
            )
            continue
        evaluated.append(event_id)

    if not evaluated:
        return set()
    async with db_session() as db:
        result = await db.execute(
            select(Entry.id)
            .join(Event, Event.entry_id == Entry.id)
            .where(Event.id.in_(evaluated), Entry.created_at >= sweep_started)
            .distinct()
        )
        return {row[0] for row in result.fetchall()}


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
def generate_entry_draft(self, entry_id: str, trigger: str = "background") -> None:
    """Draft an entry. *trigger* is "regenerate" when a person asked for it.

    Tasks queued before 25.3 carry no trigger and run as background redrafts.
    """
    # The task id keys the AI-draft reservation, so a redelivery never spends twice.
    run_sync(_generate_entry_draft(entry_id, trigger, reservation_key=self.request.id))


async def _generate_entry_draft(
    entry_id_str: str, trigger: str = "background", *, reservation_key: str | None = None
) -> None:
    from app.services.entry_location import update_place_and_weather
    from app.workers.llm import generate_draft_for_entry
    from app.workers.utils import db_session

    if trigger not in ("regenerate", "background"):
        log.warning("generate_entry_draft_unknown_trigger", entry_id=entry_id_str, trigger=trigger)
    why: Literal["regenerate", "background"] = (
        "regenerate" if trigger == "regenerate" else "background"
    )
    entry_uuid = uuid.UUID(entry_id_str)
    try:
        # Work out where the entry happened first, so the weather is for that place.
        async with db_session() as db:
            await update_place_and_weather(entry_uuid, db)
    except Exception as exc:  # never block draft on weather failure
        log.warning(
            "enrichment_weather_skipped_due_to_error",
            entry_id=entry_id_str,
            error=str(exc),
        )
    await generate_draft_for_entry(entry_uuid, trigger=why, reservation_key=reservation_key)


# ---------------------------------------------------------------------------
# refresh_entry_location (item 24)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.tasks.refresh_entry_location",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def refresh_entry_location(self, entry_id: str, refetch_weather: bool = False) -> None:
    """Work out an entry's place again and make its weather match.

    Queued when photos are added or removed, and when a person changes the
    entry's place, time zone or dates (*refetch_weather*).
    """
    try:
        run_sync(_refresh_entry_location(entry_id, refetch_weather=refetch_weather))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _refresh_entry_location(entry_id_str: str, *, refetch_weather: bool = False) -> None:
    from app.services.entry_location import update_place_and_weather
    from app.workers.utils import db_session

    async with db_session() as db:
        await update_place_and_weather(
            uuid.UUID(entry_id_str), db, refetch_weather=refetch_weather
        )


@celery_app.task(name="app.workers.tasks.refresh_diary_locations", bind=True, max_retries=3)
def refresh_diary_locations(self, diary_id: str) -> None:
    """The diary's home place changed: refresh entries that have no place of their own."""
    try:
        run_sync(_refresh_diary_locations(diary_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _refresh_diary_locations(diary_id_str: str) -> None:
    from sqlalchemy import or_, select

    from app.models import Entry
    from app.workers.utils import db_session

    async with db_session() as db:
        entry_ids = (
            await db.execute(
                select(Entry.id).where(
                    Entry.diary_id == uuid.UUID(diary_id_str),
                    Entry.deleted_at.is_(None),
                    or_(Entry.location_source.is_(None), Entry.location_source == "home"),
                )
            )
        ).scalars().all()
    for entry_id in entry_ids:
        queue_location_refresh(entry_id)


def queue_location_refresh(entry_id: uuid.UUID | str, *, refetch_weather: bool = False) -> None:
    """Queue refresh_entry_location; a queue outage is logged, never raised to the caller."""
    try:
        refresh_entry_location.delay(str(entry_id), refetch_weather=refetch_weather)
    except Exception:
        log.exception("refresh_entry_location_queue_failed", entry_id=str(entry_id))


# ---------------------------------------------------------------------------
# backfill_diary
# ---------------------------------------------------------------------------


BACKFILL_LOCK_RETRIES = 5
BACKFILL_LOCK_RETRY_SECONDS = 60
BACKFILL_LOCKED_MESSAGE = (
    "Another scan was still running. Try the backfill again in a few minutes."
)


@celery_app.task(
    name="app.workers.tasks.backfill_diary",
    bind=True,
    # Retries are used only to wait for the scan lock (BackfillLocked).
    max_retries=BACKFILL_LOCK_RETRIES,
    default_retry_delay=BACKFILL_LOCK_RETRY_SECONDS,
)
def backfill_diary(self, backfill_run_id: str) -> None:
    from app.workers.backfill import BackfillLocked

    try:
        run_sync(_backfill_diary(backfill_run_id))
    except BackfillLocked:
        # _backfill_diary has set the run back to pending, so the retried
        # delivery gets past its not-pending guard.
        try:
            raise self.retry(
                countdown=BACKFILL_LOCK_RETRY_SECONDS, max_retries=BACKFILL_LOCK_RETRIES
            ) from None
        except self.MaxRetriesExceededError:
            run_sync(_fail_locked_backfill(backfill_run_id))


async def _fail_locked_backfill(backfill_run_id_str: str) -> None:
    """The scan lock never came free: fail a run that is still waiting."""
    from sqlalchemy import select

    from app.models import BackfillRun
    from app.workers.utils import db_session

    async with db_session() as db:
        run = (
            await db.execute(
                select(BackfillRun).where(BackfillRun.id == uuid.UUID(backfill_run_id_str))
            )
        ).scalar_one_or_none()
        if run is None or run.status != "pending":
            return  # cancelled, or deleted with its diary, while it waited
        run.status = "failed"
        run.completed_at = datetime.now(tz=UTC)
        run.error = BACKFILL_LOCKED_MESSAGE


_BACKFILL_DISCONNECTED_MESSAGE = (
    "Google Calendar was disconnected during the backfill. "
    "Reconnect it and run the backfill again."
)
_BACKFILL_FAILED_MESSAGE = "The backfill stopped because of a problem. Try again later."


async def _backfill_diary(backfill_run_id_str: str) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models import BackfillRun, Diary, OAuthToken
    from app.services.read_only import is_diary_read_only
    from app.workers.backfill import BackfillLocked, ingest_failures_message, run_backfill
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

        if await is_diary_read_only(db, diary):
            log.info(
                "diary_read_only_skip", diary_id=str(diary.id), where="backfill"
            )
            return

        token_result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == diary.owner_user_id,
                OAuthToken.provider == "google",
            )
        )
        oauth_token = token_result.scalar_one_or_none()

        if backfill_run.status != "pending":
            # Cancelled before the worker got to it, or a duplicate delivery.
            log.info(
                "backfill_not_pending",
                backfill_run_id=backfill_run_id_str,
                status=backfill_run.status,
            )
            return

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
        oauth_token_fresh = token_result.scalar_one_or_none()
        if oauth_token_fresh is None:
            access_token = None
        else:
            access_token = await ensure_fresh_access_token(oauth_token_fresh, db)

    if oauth_token_fresh is None:
        # Disconnected while the backfill waited to start.
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = "No valid Google OAuth token"
        return

    if not access_token:
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = "Failed to refresh Google access token"
        return

    try:
        counts = await run_backfill(
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
            # A run cancelled mid-flight stops at the next chunk boundary; keep
            # its "cancelled" status instead of overwriting it.
            if run.status != "cancelled":
                run.status = "completed"
                run.completed_at = datetime.now(tz=UTC)
            run.events_ingested = counts.events_ingested
            run.entries_created = counts.entries_created
            run.error = ingest_failures_message(counts.events_failed)
    except BackfillLocked:
        # A scan holds the lock. Put the run back to waiting; the task retries.
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            if run.status == "running":
                run.status = "pending"
                run.started_at = None
        raise
    except Exception as e:
        # Never log or store str(e): an HTTP error's text carries the Calendar URL,
        # and the calendar id is often an email address.
        import httpx

        log_extra: dict[str, object] = {"error_class": type(e).__name__}
        if isinstance(e, httpx.HTTPStatusError):
            log_extra["status_code"] = e.response.status_code
        log.error("backfill_error", backfill_run_id=backfill_run_id_str, **log_extra)
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403):
            message = _BACKFILL_DISCONNECTED_MESSAGE
        else:
            message = _BACKFILL_FAILED_MESSAGE
        async with db_session() as db:
            result = await db.execute(select(BackfillRun).where(BackfillRun.id == backfill_run_id))
            run = result.scalar_one()
            run.status = "failed"
            run.completed_at = datetime.now(tz=UTC)
            run.error = message
        raise



# ---------------------------------------------------------------------------
# process_photo_import (item 14: Google Photos picks)
# ---------------------------------------------------------------------------


@celery_app.task(name="app.workers.tasks.process_photo_import", bind=True)
def process_photo_import(
    self, import_id: str, list_attempt: int = 0, poll_s: float | None = None
) -> None:
    """Poll a Google Photos pick until it's done, then copy the photos onto the entry.

    Re-queues itself at Google's poll interval, so it finishes without the
    parent's page open.
    """
    run_sync(_process_photo_import(import_id, list_attempt=list_attempt, poll_s=poll_s))


async def _process_photo_import(
    import_id_str: str, list_attempt: int = 0, poll_s: float | None = None
) -> None:
    from app.workers.photo_imports import mark_failed, run_photo_import

    import_id = uuid.UUID(import_id_str)
    try:
        nxt = await run_photo_import(import_id, list_attempt=list_attempt, poll_s=poll_s)
    except Exception:
        # Nothing re-queues a crashed run: end it visibly rather than leave it waiting.
        log.exception("photo_import_crashed", import_id=import_id_str)
        try:
            await mark_failed(import_id)
        except Exception:
            log.exception("photo_import_mark_failed_failed", import_id=import_id_str)
        raise
    if nxt is None:
        return
    try:
        process_photo_import.apply_async(
            args=(import_id_str,),
            kwargs={"list_attempt": nxt.list_attempt, "poll_s": nxt.poll_s},
            countdown=nxt.countdown,
        )
    except Exception:
        # Nothing else would ever pick it up again: say so instead of waiting forever.
        log.exception("photo_import_requeue_failed", import_id=import_id_str)
        await mark_failed(import_id)
