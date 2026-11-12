"""Rule engine: condition tree matcher + rules evaluator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as _date

import structlog
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutoCreationRule,
    Diary,
    Entry,
    EntryRuleMatch,
    Event,
    RuleSeriesClaim,
    User,
)
from app.services.tier import try_enforce_entry_tier_limit
from app.workers.tz_utils import google_event_to_entry_date

log = structlog.get_logger()


def match_event(condition: dict, payload: dict) -> bool:
    """Recursively evaluate a condition tree against an event payload.

    Empty groups → False. Empty leaf value → False.
    """
    op = condition.get("op")

    if op in ("AND", "OR"):
        children = condition.get("children") or []
        if not children:
            return False
        if op == "AND":
            return all(match_event(child, payload) for child in children)
        return any(match_event(child, payload) for child in children)

    # Leaf node
    field = condition.get("field", "")
    leaf_op = condition.get("op", "contains")
    value: str = condition.get("value", "")
    case_sensitive: bool = condition.get("case_sensitive", False)

    if not value:
        return False

    if field == "attendee_email":
        attendees = payload.get("attendees") or []
        emails = [str(a.get("email", "")) for a in attendees if isinstance(a, dict)]
        return _match_any(emails, leaf_op, value, case_sensitive)

    field_map = {
        "title": "summary",
        "description": "description",
        "location": "location",
    }
    payload_key = field_map.get(field, field)
    field_value = str(payload.get(payload_key) or "")
    return _match_string(field_value, leaf_op, value, case_sensitive)


def _match_string(field_value: str, op: str, value: str, case_sensitive: bool) -> bool:
    if not case_sensitive:
        field_value = field_value.lower()
        value = value.lower()
    if op == "contains":
        return value in field_value
    if op == "equals":
        return field_value == value
    if op == "not_contains":
        return value not in field_value
    return False


def _match_any(values: list[str], op: str, value: str, case_sensitive: bool) -> bool:
    if op == "not_contains":
        return all(_match_string(v, op, value, case_sensitive) for v in values)
    return any(_match_string(v, op, value, case_sensitive) for v in values)


# ---------------------------------------------------------------------------
# Rules evaluator
# ---------------------------------------------------------------------------


async def evaluate_event_against_rules(
    event_id: str, diary_id: str, db: AsyncSession
) -> None:
    """Run the diary's enabled auto-creation rules against *event_id*.

    An event gets at most one entry. Every matching rule is recorded against
    that entry; when they disagree about grouping, the oldest rule decides.
    An event that has not ended yet is left alone, to be evaluated later.

    Queues ``generate_entry_draft`` after commit, once per entry that needs it.
    """
    # Import here to avoid circular imports (tasks → rules → tasks)
    from app.workers.tasks import generate_entry_draft  # noqa: PLC0415

    event_uuid = uuid.UUID(event_id)
    diary_uuid = uuid.UUID(diary_id)

    # Row lock: two evaluations of one event must not both create an entry.
    result = await db.execute(
        select(Event).where(Event.id == event_uuid).with_for_update()
    )
    event = result.scalar_one_or_none()
    if event is None:
        log.warning("evaluate_rules_event_not_found", event_id=event_id)
        return

    if event.entry_id is not None:
        return

    now = datetime.now(tz=UTC)
    if event.ends_at is not None and event.ends_at > now:
        # Not over yet. The scan after it ends picks it up.
        return

    # From here on the event counts as evaluated, whatever the outcome.
    event.rules_evaluated_at = now

    rules_result = await db.execute(
        select(AutoCreationRule)
        .where(AutoCreationRule.diary_id == diary_uuid)
        .where(AutoCreationRule.enabled.is_(True))
        # Oldest first: the first matching rule decides how the event is grouped.
        .order_by(AutoCreationRule.created_at.asc(), AutoCreationRule.id.asc())
    )
    payload = event.payload or {}
    matching = [rule for rule in rules_result.scalars() if match_event(rule.condition, payload)]

    entry_id: uuid.UUID | None = None
    needs_draft = False
    if matching:
        entry_id, needs_draft = await _entry_for_event(db, event, diary_uuid, matching[0])

    if entry_id is not None:
        event.entry_id = entry_id
        for rule in matching:
            # Several events can share an entry, so the pair may already be recorded.
            already = await db.execute(
                select(EntryRuleMatch).where(
                    EntryRuleMatch.entry_id == entry_id,
                    EntryRuleMatch.rule_id == rule.id,
                )
            )
            if already.scalar_one_or_none() is None:
                db.add(EntryRuleMatch(entry_id=entry_id, rule_id=rule.id))

    # Commit first: a worker that picks the draft task up before this
    # transaction commits cannot see the entry and gives up.
    await db.commit()

    if entry_id is not None and needs_draft:
        try:
            generate_entry_draft.delay(str(entry_id))
        except Exception:
            log.exception("evaluate_rules_llm_queue_failed", entry_id=str(entry_id))


async def _entry_for_event(
    db: AsyncSession, event: Event, diary_id: uuid.UUID, rule: AutoCreationRule
) -> tuple[uuid.UUID | None, bool]:
    """Find or create the entry *event* belongs to, grouped as *rule* says.

    Returns ``(entry_id, needs_draft)``. ``entry_id`` is None when a new entry
    was needed but the owner's tier limit does not allow one.
    """
    diary = (await db.execute(select(Diary).where(Diary.id == diary_id))).scalar_one_or_none()
    if diary is None:
        return None, False
    user = (
        await db.execute(select(User).where(User.id == diary.owner_user_id))
    ).scalar_one_or_none()
    if user is None:
        return None, False

    payload = event.payload or {}
    raw_event = {"start": payload.get("start", {}), "end": payload.get("end", {})}
    entry_date, entry_end_date = google_event_to_entry_date(raw_event, diary.timezone)
    if entry_date is None:
        entry_date = event.occurred_at.date() if event.occurred_at else _date.today()

    options = rule.options or {}
    recurring_event_id: str | None = payload.get("recurringEventId")
    per_series = (
        options.get("recurring", "per_instance") == "per_series"
        and recurring_event_id is not None
    )
    spanning = options.get("multi_day") == "spanning"

    async def _new_entry(end_date: _date | None) -> uuid.UUID | None:
        ok, reason = await try_enforce_entry_tier_limit(
            owner_user_id=user.id,
            source="auto",
            db=db,
            owner_subscription_tier=user.subscription_tier,
        )
        if not ok:
            log.warning("evaluate_rules_tier_limit", rule_id=str(rule.id), reason=reason)
            return None
        entry = Entry(
            diary_id=diary_id,
            entry_date=entry_date,
            entry_end_date=end_date,
            status="draft",
            created_by="auto",
            creation_source="rule",
        )
        db.add(entry)
        await db.flush()
        return entry.id

    if per_series:
        # One entry for the whole recurring series, tracked by a claim.
        claim = (
            await db.execute(
                select(RuleSeriesClaim)
                .where(RuleSeriesClaim.rule_id == rule.id)
                .where(RuleSeriesClaim.recurring_event_id == recurring_event_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if claim is not None:
            return claim.entry_id, False

        new_id = await _new_entry(None)  # series entries don't span
        if new_id is None:
            return None, False
        db.add(
            RuleSeriesClaim(
                rule_id=rule.id, recurring_event_id=recurring_event_id, entry_id=new_id
            )
        )
        return new_id, True

    if spanning:
        # Join an existing rule-created entry with exactly the same date range
        # (NULL end matches NULL end). Manual entries are never grouped into.
        end_predicate: ColumnElement[bool] = (
            Entry.entry_end_date.is_(None)
            if entry_end_date is None
            else Entry.entry_end_date == entry_end_date
        )
        existing = (
            await db.execute(
                select(Entry)
                .where(
                    and_(
                        Entry.diary_id == diary_id,
                        Entry.entry_date == entry_date,
                        end_predicate,
                        Entry.creation_source == "rule",
                        Entry.deleted_at.is_(None),
                    )
                )
                .order_by(Entry.created_at.asc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info(
                "rules_event_grouped",
                entry_id=str(existing.id),
                event_id=str(event.id),
                diary_id=str(diary_id),
                rule_id=str(rule.id),
                entry_date=str(entry_date),
                entry_end_date=str(entry_end_date) if entry_end_date else None,
            )
            # Redraft so the text covers the event that just joined. Known
            # follow-up: N grouped events in one scan mean N regenerations.
            return existing.id, True

    new_id = await _new_entry(entry_end_date if spanning else None)
    return new_id, new_id is not None
