"""Rule engine: condition tree matcher + rules evaluator."""

from __future__ import annotations

import uuid
from datetime import date as _date

import structlog
from sqlalchemy import and_, select
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
    """Evaluate all enabled auto-creation rules for the diary against *event_id*.

    For each matching rule:
    - ``per_series`` with a ``recurringEventId``: look up or create a
      ``RuleSeriesClaim`` and attach to its entry.
    - Everything else (``per_instance``, ``multi_day``, no recurring ID):
      create a new Entry for this event.

    Queues ``generate_entry_draft`` only for freshly created entries.
    Commits once at the end.
    """
    # Import here to avoid circular imports (tasks → rules → tasks)
    from app.workers.tasks import generate_entry_draft  # noqa: PLC0415

    event_uuid = uuid.UUID(event_id)
    diary_uuid = uuid.UUID(diary_id)

    # 1. Load the event with a row-level lock to prevent concurrent races.
    result = await db.execute(
        select(Event).where(Event.id == event_uuid).with_for_update()
    )
    event = result.scalar_one_or_none()
    if event is None:
        log.warning("evaluate_rules_event_not_found", event_id=event_id)
        return

    # Skip events that are already attached to an entry.
    if event.entry_id is not None:
        return

    # 2. Load enabled rules for this diary.
    rules_result = await db.execute(
        select(AutoCreationRule)
        .where(AutoCreationRule.diary_id == diary_uuid)
        .where(AutoCreationRule.enabled.is_(True))
    )
    rules = list(rules_result.scalars())
    if not rules:
        return

    # 3. Load diary (needed for timezone + user_id).
    diary_result = await db.execute(select(Diary).where(Diary.id == diary_uuid))
    diary = diary_result.scalar_one_or_none()
    if diary is None:
        return

    user_result = await db.execute(select(User).where(User.id == diary.owner_user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return

    # Build date info once — reused across rules.
    p = event.payload or {}
    raw_event = {"start": p.get("start", {}), "end": p.get("end", {})}
    entry_date, entry_end_date = google_event_to_entry_date(raw_event, diary.timezone)
    if entry_date is None:
        entry_date = event.occurred_at.date() if event.occurred_at else _date.today()

    recurring_event_id: str | None = p.get("recurringEventId")

    # Track the first entry attached to the event across all rules so that
    # event.entry_id is set exactly once.
    first_attached_entry_id: uuid.UUID | None = None

    for rule in rules:
        if not match_event(rule.condition, p):
            continue

        options = rule.options or {}
        is_per_series = (
            options.get("recurring", "per_instance") == "per_series"
            and recurring_event_id is not None
        )

        new_entry: Entry | None = None
        entry_id_to_use: uuid.UUID | None = None

        if is_per_series:
            # ----------------------------------------------------------------
            # per_series path: look up or create a RuleSeriesClaim.
            # ----------------------------------------------------------------
            claim_result = await db.execute(
                select(RuleSeriesClaim)
                .where(RuleSeriesClaim.rule_id == rule.id)
                .where(RuleSeriesClaim.recurring_event_id == recurring_event_id)
                .with_for_update()
            )
            claim = claim_result.scalar_one_or_none()

            if claim is not None:
                # Existing series entry — reuse it.
                entry_id_to_use = claim.entry_id
            else:
                # First instance — check tier limit, create entry + claim.
                ok, reason = await try_enforce_entry_tier_limit(
                    owner_user_id=user.id,
                    source="auto",
                    db=db,
                    owner_subscription_tier=user.subscription_tier,
                )
                if not ok:
                    log.warning(
                        "evaluate_rules_tier_limit",
                        rule_id=str(rule.id),
                        reason=reason,
                    )
                    continue

                new_entry = Entry(
                    diary_id=diary_uuid,
                    entry_date=entry_date,
                    entry_end_date=None,  # series entries don't span
                    status="draft",
                    created_by="auto",
                    creation_source="rule",
                )
                db.add(new_entry)
                await db.flush()
                entry_id_to_use = new_entry.id

                claim = RuleSeriesClaim(
                    rule_id=rule.id,
                    recurring_event_id=recurring_event_id,
                    entry_id=entry_id_to_use,
                )
                db.add(claim)
        else:
            # ----------------------------------------------------------------
            # per_instance / multi_day path. For multi_day == "spanning",
            # group into an existing rule-created entry that has the EXACT
            # same (entry_date, entry_end_date) range — including NULL == NULL.
            # Manual entries are never grouped into.
            # ----------------------------------------------------------------
            use_end_date = (
                entry_end_date if options.get("multi_day") == "spanning" else None
            )

            existing_entry: Entry | None = None
            if options.get("multi_day") == "spanning":
                # NULL-safe equality on entry_end_date:
                #   - if use_end_date is None: match rows where entry_end_date IS NULL
                #   - else: match rows where entry_end_date == use_end_date
                if use_end_date is None:
                    end_predicate = Entry.entry_end_date.is_(None)
                else:
                    end_predicate = Entry.entry_end_date == use_end_date

                existing_result = await db.execute(
                    select(Entry)
                    .where(
                        and_(
                            Entry.diary_id == diary_uuid,
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
                existing_entry = existing_result.scalar_one_or_none()

            if existing_entry is not None:
                # Reuse — skip tier check (no new entry being created).
                entry_id_to_use = existing_entry.id
                log.info(
                    "rules_event_grouped",
                    entry_id=str(entry_id_to_use),
                    event_id=event_id,
                    diary_id=diary_id,
                    rule_id=str(rule.id),
                    entry_date=str(entry_date),
                    entry_end_date=str(use_end_date) if use_end_date else None,
                )
                # Re-queue draft regeneration so the LLM produces a coherent
                # multi-event entry. Known follow-up: dedupe these calls at
                # the LLM-task layer so a scan window with N grouped events
                # does not produce N regenerations.
                try:
                    generate_entry_draft.delay(str(entry_id_to_use))
                except Exception:
                    log.exception(
                        "evaluate_rules_llm_queue_failed",
                        entry_id=str(entry_id_to_use),
                    )
            else:
                ok, reason = await try_enforce_entry_tier_limit(
                    owner_user_id=user.id,
                    source="auto",
                    db=db,
                    owner_subscription_tier=user.subscription_tier,
                )
                if not ok:
                    log.warning(
                        "evaluate_rules_tier_limit",
                        rule_id=str(rule.id),
                        reason=reason,
                    )
                    continue

                new_entry = Entry(
                    diary_id=diary_uuid,
                    entry_date=entry_date,
                    entry_end_date=use_end_date,
                    status="draft",
                    created_by="auto",
                    creation_source="rule",
                )
                db.add(new_entry)
                await db.flush()
                entry_id_to_use = new_entry.id

        # 4. Attach event to entry (only the first time across all rules).
        if entry_id_to_use is not None:
            if first_attached_entry_id is None:
                event.entry_id = entry_id_to_use
                first_attached_entry_id = entry_id_to_use

            # 5. Record the rule match — but only if one doesn't already exist
            # (per_series: multiple events share an entry, so the (entry_id, rule_id)
            # pair may already have been inserted by an earlier instance).
            existing_match_result = await db.execute(
                select(EntryRuleMatch).where(
                    EntryRuleMatch.entry_id == entry_id_to_use,
                    EntryRuleMatch.rule_id == rule.id,
                )
            )
            if existing_match_result.scalar_one_or_none() is None:
                match_row = EntryRuleMatch(entry_id=entry_id_to_use, rule_id=rule.id)
                db.add(match_row)

            # 6. Queue LLM generation only for newly created entries.
            if new_entry is not None:
                try:
                    generate_entry_draft.delay(str(new_entry.id))
                except Exception:
                    log.exception(
                        "evaluate_rules_llm_queue_failed",
                        entry_id=str(new_entry.id),
                    )

    # 7. Commit all changes in a single transaction.
    await db.commit()
