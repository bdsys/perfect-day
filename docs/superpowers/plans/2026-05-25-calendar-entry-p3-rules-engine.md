# Calendar Entry Refactor — Part 3: Rules Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisites:** Parts 1 and 2 must be complete. `Event.diary_id` is set on ingest; `Event.entry_id` is nullable; `AutoCreationRule`, `EntryRuleMatch`, `RuleSeriesClaim` tables exist; `try_enforce_entry_tier_limit` exists in `services/tier.py`.

**Goal:** Implement the full auto-creation rules engine: a pure `match_event` function (TDD first), a `evaluate_event_against_rules` async helper, a Celery task that replaces the stub in `tasks.py`, a backfill task, a complete rules CRUD + preview REST API, and the rules list and form pages in Next.js.

**Architecture:** New `apps/api/app/workers/rules.py` contains all pure rule logic. The Celery task `evaluate_rules_for_event` in `tasks.py` delegates to it. A new `apps/api/app/routers/v1/rules.py` handles CRUD and preview. The frontend adds three new pages and updates `EntryOut` in the API client.

**Tech Stack:** Python, SQLAlchemy async, Celery, pytest; Next.js 14 App Router, TypeScript

---

## Condition tree JSON shape (reference for all tasks below)

```
group := { "op": "AND" | "OR", "children": [node, ...] }
leaf  := { "field": "title" | "description" | "location" | "attendee_email",
           "op": "contains" | "equals" | "not_contains",
           "value": "string",
           "case_sensitive": false }
```

Constraints: max depth 5, max 20 children per group, max 50 total leaves. Empty group → false. Empty `value` → false. `attendee_email` iterates `payload.attendees[*].email`, matches if any attendee satisfies the condition.

## File Map

| Action | Path |
|---|---|
| **Create** | `apps/api/app/workers/rules.py` |
| **Modify** | `apps/api/app/workers/tasks.py` (replace stub, add backfill task) |
| **Create** | `apps/api/app/routers/v1/rules.py` |
| **Modify** | `apps/api/app/main.py` |
| **Modify** | `apps/api/app/routers/v1/entries.py` (add `rule_matches` to `EntryOut`) |
| **Create** | `apps/api/tests/unit/test_rule_matcher.py` |
| **Create** | `apps/api/tests/integration/test_rules_endpoint.py` |
| **Create** | `apps/api/tests/integration/test_rules_evaluation.py` |
| **Create** | `apps/api/tests/integration/test_rule_tier_limit.py` |
| **Create** | `apps/api/tests/integration/test_rule_backfill.py` |
| **Create** | `apps/api/tests/integration/test_rules_per_series.py` |
| **Modify** | `apps/web/src/lib/api.ts` |
| **Create** | `apps/web/src/app/diaries/[diaryId]/rules/page.tsx` |
| **Create** | `apps/web/src/app/diaries/[diaryId]/rules/new/page.tsx` |
| **Create** | `apps/web/src/app/rules/[ruleId]/page.tsx` |
| **Create** | `apps/web/src/components/RuleForm.tsx` |
| **Modify** | `apps/web/src/app/entries/[entryId]/page.tsx` |

---

### Task 1: Pure rule matcher (TDD)

**Files:**
- Create: `apps/api/tests/unit/test_rule_matcher.py`
- Create: `apps/api/app/workers/rules.py` (matcher only)

- [ ] **Step 1: Write all matcher unit tests**

Create `apps/api/tests/unit/test_rule_matcher.py`:

```python
"""Unit tests for the condition tree matcher (match_event)."""

from __future__ import annotations

import pytest

# Import lazily so tests fail with ImportError until the module exists
from app.workers.rules import match_event


def _payload(**kwargs) -> dict:
    return {
        "summary": kwargs.get("summary", ""),
        "description": kwargs.get("description", ""),
        "location": kwargs.get("location", ""),
        "attendees": kwargs.get("attendees", []),
    }


# ---------------------------------------------------------------------------
# Leaf: title contains
# ---------------------------------------------------------------------------


def test_title_contains_match():
    condition = {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_contains_no_match():
    condition = {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Piano lesson")) is False


def test_title_contains_case_sensitive_mismatch():
    condition = {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": True}
    assert match_event(condition, _payload(summary="soccer practice")) is False


def test_title_contains_case_sensitive_match():
    condition = {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": True}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_equals_match():
    condition = {"field": "title", "op": "equals", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer")) is True


def test_title_equals_no_match():
    condition = {"field": "title", "op": "equals", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is False


def test_title_not_contains_match():
    condition = {"field": "title", "op": "not_contains", "value": "zoom", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_not_contains_no_match():
    condition = {"field": "title", "op": "not_contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is False


# ---------------------------------------------------------------------------
# Empty value → always false
# ---------------------------------------------------------------------------


def test_empty_value_always_false():
    condition = {"field": "title", "op": "contains", "value": "", "case_sensitive": False}
    assert match_event(condition, _payload(summary="anything")) is False


# ---------------------------------------------------------------------------
# Missing field in payload → treated as empty string
# ---------------------------------------------------------------------------


def test_missing_field_no_match():
    condition = {"field": "location", "op": "contains", "value": "park", "case_sensitive": False}
    assert match_event(condition, {}) is False


def test_missing_field_not_contains_match():
    condition = {"field": "location", "op": "not_contains", "value": "zoom", "case_sensitive": False}
    assert match_event(condition, {}) is True


# ---------------------------------------------------------------------------
# attendee_email field
# ---------------------------------------------------------------------------


def test_attendee_email_any_matches():
    condition = {"field": "attendee_email", "op": "contains", "value": "alice", "case_sensitive": False}
    attendees = [{"email": "alice@example.com"}, {"email": "bob@example.com"}]
    assert match_event(condition, _payload(attendees=attendees)) is True


def test_attendee_email_none_matches():
    condition = {"field": "attendee_email", "op": "contains", "value": "charlie", "case_sensitive": False}
    attendees = [{"email": "alice@example.com"}, {"email": "bob@example.com"}]
    assert match_event(condition, _payload(attendees=attendees)) is False


def test_attendee_email_empty_list_false():
    condition = {"field": "attendee_email", "op": "contains", "value": "alice", "case_sensitive": False}
    assert match_event(condition, _payload(attendees=[])) is False


# ---------------------------------------------------------------------------
# AND / OR groups
# ---------------------------------------------------------------------------


def test_and_both_true():
    condition = {
        "op": "AND",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "location", "op": "contains", "value": "park", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is True


def test_and_one_false():
    condition = {
        "op": "AND",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "location", "op": "contains", "value": "zoom", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is False


def test_or_one_true():
    condition = {
        "op": "OR",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "title", "op": "contains", "value": "piano", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Piano lesson")) is True


def test_or_both_false():
    condition = {
        "op": "OR",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "title", "op": "contains", "value": "piano", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Dentist appointment")) is False


def test_nested_and_of_or():
    condition = {
        "op": "AND",
        "children": [
            {
                "op": "OR",
                "children": [
                    {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
                    {"field": "title", "op": "contains", "value": "swimming", "case_sensitive": False},
                ],
            },
            {"field": "location", "op": "contains", "value": "park", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is True
    assert match_event(condition, _payload(summary="Soccer practice", location="Indoor gym")) is False
    assert match_event(condition, _payload(summary="Dentist", location="City Park")) is False


# ---------------------------------------------------------------------------
# Empty group → false
# ---------------------------------------------------------------------------


def test_empty_and_group_false():
    condition = {"op": "AND", "children": []}
    assert match_event(condition, _payload(summary="anything")) is False


def test_empty_or_group_false():
    condition = {"op": "OR", "children": []}
    assert match_event(condition, _payload(summary="anything")) is False
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd apps/api && pytest tests/unit/test_rule_matcher.py -v
```

Expected: ImportError or all FAIL.

- [ ] **Step 3: Implement `match_event` in `rules.py`**

Create `apps/api/app/workers/rules.py`:

```python
"""Rule engine: condition tree matcher and event evaluation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Condition tree matcher
# ---------------------------------------------------------------------------


def match_event(condition: dict, payload: dict) -> bool:
    """Recursively evaluate a condition tree against an event payload.

    Returns True if the condition matches, False otherwise.
    Empty groups always return False (vacuous truth would be surprising).
    Empty leaf values always return False.
    """
    op = condition.get("op")

    if op in ("AND", "OR"):
        children = condition.get("children") or []
        if not children:
            return False
        if op == "AND":
            return all(match_event(child, payload) for child in children)
        else:  # OR
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

    # Map field name to payload key
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
    elif op == "equals":
        return field_value == value
    elif op == "not_contains":
        return value not in field_value
    return False


def _match_any(values: list[str], op: str, value: str, case_sensitive: bool) -> bool:
    return any(_match_string(v, op, value, case_sensitive) for v in values)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd apps/api && pytest tests/unit/test_rule_matcher.py -v
```

Expected: all 20 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/rules.py apps/api/tests/unit/test_rule_matcher.py
git commit -m "feat: add rule condition tree matcher (match_event) with full unit tests"
```

---

### Task 2: `evaluate_event_against_rules` async helper

**Files:**
- Modify: `apps/api/app/workers/rules.py`

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_rules_evaluation.py`:

```python
"""Integration test: scan with rules creates entries for matching events only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry, EntryRuleMatch, Event
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_diary, make_event, make_user


@pytest.fixture()
def soccer_rule_condition() -> dict:
    return {
        "op": "AND",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
        ],
    }


@pytest.fixture()
def soccer_rule_options() -> dict:
    return {"recurring": "per_instance", "multi_day": "spanning"}


class TestEvaluateEventAgainstRules:
    async def test_matching_event_creates_entry(
        self,
        db_session: AsyncSession,
        soccer_rule_condition,
        soccer_rule_options,
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Soccer",
            enabled=True,
            condition=soccer_rule_condition,
            options=soccer_rule_options,
        )
        db_session.add(rule)
        await db_session.commit()

        ev = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                "summary": "Soccer practice",
                "description": "",
                "location": "Park",
                "start": {"dateTime": "2026-05-20T10:00:00-05:00"},
                "end": {"dateTime": "2026-05-20T11:30:00-05:00"},
                "status": "confirmed",
                "attendees": [],
                "recurringEventId": None,
            },
            occurred_at=datetime(2026, 5, 20, 15, 0, tzinfo=UTC),
        )

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            with patch("app.workers.rules.try_enforce_entry_tier_limit", new=AsyncMock(return_value=(True, None))):
                await evaluate_event_against_rules(str(ev.id), str(diary.id), db_session)

        await db_session.refresh(ev)
        assert ev.entry_id is not None

        result = await db_session.execute(select(Entry).where(Entry.id == ev.entry_id))
        entry = result.scalar_one()
        assert entry.status == "draft"
        assert entry.created_by == "auto"
        assert entry.creation_source == "rule"

        match_result = await db_session.execute(
            select(EntryRuleMatch).where(
                EntryRuleMatch.entry_id == ev.entry_id,
                EntryRuleMatch.rule_id == rule.id,
            )
        )
        assert match_result.scalar_one_or_none() is not None

    async def test_non_matching_event_stays_unattached(
        self,
        db_session: AsyncSession,
        soccer_rule_condition,
        soccer_rule_options,
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Soccer",
            enabled=True,
            condition=soccer_rule_condition,
            options=soccer_rule_options,
        )
        db_session.add(rule)
        await db_session.commit()

        ev = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                "summary": "Dentist appointment",
                "description": "",
                "location": "",
                "start": {"dateTime": "2026-05-20T10:00:00-05:00"},
                "end": {},
                "status": "confirmed",
                "attendees": [],
                "recurringEventId": None,
            },
        )

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            with patch("app.workers.rules.try_enforce_entry_tier_limit", new=AsyncMock(return_value=(True, None))):
                await evaluate_event_against_rules(str(ev.id), str(diary.id), db_session)

        await db_session.refresh(ev)
        assert ev.entry_id is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd apps/api && pytest tests/integration/test_rules_evaluation.py -v
```

Expected: FAIL — `evaluate_event_against_rules` not yet defined in `rules.py`.

- [ ] **Step 3: Implement `evaluate_event_against_rules`**

Append to `apps/api/app/workers/rules.py`:

```python
# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


async def evaluate_event_against_rules(
    event_id: str,
    diary_id: str,
    db,
) -> None:
    """Evaluate enabled rules for a newly ingested event.

    For each matching rule, creates an Entry (respecting recurring/multi_day options
    and per-series claims), enforces tier limits, queues LLM generation for new entries,
    and records EntryRuleMatch rows.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import AutoCreationRule, Entry, EntryRuleMatch, RuleSeriesClaim
    from app.services.tier import try_enforce_entry_tier_limit
    from app.workers.tasks import generate_entry_draft
    from app.workers.tz_utils import google_event_to_entry_date

    event_uuid = uuid.UUID(event_id)
    diary_uuid = uuid.UUID(diary_id)

    # Lock the event row to prevent race with backfill
    from sqlalchemy import text
    from app.models import Event, Diary

    event_result = await db.execute(
        select(Event).where(Event.id == event_uuid).with_for_update()
    )
    event = event_result.scalar_one_or_none()
    if event is None or event.diary_id != diary_uuid:
        return

    diary_result = await db.execute(
        select(Diary).where(Diary.id == diary_uuid, Diary.deleted_at.is_(None))
    )
    diary = diary_result.scalar_one_or_none()
    if diary is None:
        return

    # Load enabled rules
    rules_result = await db.execute(
        select(AutoCreationRule).where(
            AutoCreationRule.diary_id == diary_uuid,
            AutoCreationRule.enabled.is_(True),
        )
    )
    rules = rules_result.scalars().all()
    if not rules:
        return

    p = event.payload or {}
    diary_timezone = diary.timezone
    raw_event = {"start": p.get("start", {}), "end": p.get("end", {})}
    entry_date, entry_end_date = google_event_to_entry_date(raw_event, diary_timezone)
    if entry_date is None:
        return

    recurring_event_id: str | None = p.get("recurringEventId")

    # Track entries created in this evaluation so multiple rules can point to the same entry
    # key: (entry_date, recurring_event_id or None) for per-instance dedup
    date_entry_map: dict[tuple, uuid.UUID] = {}
    entries_created_ids: set[uuid.UUID] = set()

    for rule in rules:
        if not match_event(rule.condition, p):
            continue

        options = rule.options or {}
        recurring_opt = options.get("recurring", "per_instance")
        multi_day_opt = options.get("multi_day", "spanning")

        target_entry_id: uuid.UUID | None = None

        # Per-series: check for an existing claim
        if recurring_opt == "per_series" and recurring_event_id:
            claim_result = await db.execute(
                select(RuleSeriesClaim).where(
                    RuleSeriesClaim.rule_id == rule.id,
                    RuleSeriesClaim.recurring_event_id == recurring_event_id,
                )
            )
            existing_claim = claim_result.scalar_one_or_none()
            if existing_claim is not None:
                target_entry_id = existing_claim.entry_id
            # else: will create new entry and insert claim below

        if target_entry_id is None:
            # Check if we already created an entry for this date in this evaluation pass
            dedup_key = (entry_date, recurring_event_id if recurring_opt == "per_series" else None)
            if dedup_key in date_entry_map:
                target_entry_id = date_entry_map[dedup_key]
            else:
                # Tier check before creating
                ok, reason = await try_enforce_entry_tier_limit(
                    user_id=diary.owner_user_id,
                    diary_id=diary_uuid,
                    source="auto",
                    db=db,
                    subscription_tier=diary.owner.subscription_tier if hasattr(diary, "owner") else "free",
                )
                if not ok:
                    log.warning(
                        "rule_tier_limit_reached",
                        diary_id=str(diary_uuid),
                        rule_id=str(rule.id),
                        reason=reason,
                    )
                    continue

                entry = Entry(
                    diary_id=diary_uuid,
                    entry_date=entry_date,
                    entry_end_date=entry_end_date if multi_day_opt == "spanning" else None,
                    status="draft",
                    created_by="auto",
                    creation_source="rule",
                )
                db.add(entry)
                await db.flush()
                target_entry_id = entry.id
                date_entry_map[dedup_key] = target_entry_id
                entries_created_ids.add(target_entry_id)

                # Insert series claim if per_series
                if recurring_opt == "per_series" and recurring_event_id:
                    claim = RuleSeriesClaim(
                        rule_id=rule.id,
                        recurring_event_id=recurring_event_id,
                        entry_id=target_entry_id,
                    )
                    db.add(claim)

        # Attach event to entry (only once — first rule wins attachment)
        if event.entry_id is None:
            event.entry_id = target_entry_id
            await db.flush()

        # Record rule match
        match_row = EntryRuleMatch(
            entry_id=target_entry_id,
            rule_id=rule.id,
        )
        db.add(match_row)

    await db.flush()

    # Queue LLM generation for newly created entries
    for new_entry_id in entries_created_ids:
        generate_entry_draft.delay(str(new_entry_id))
```

Note: `diary.owner.subscription_tier` requires the owner relationship to be loaded. Add a `selectinload` or load the owner separately:

Replace the diary query with:

```python
    from sqlalchemy.orm import selectinload
    diary_result = await db.execute(
        select(Diary)
        .options(selectinload(Diary.owner))
        .where(Diary.id == diary_uuid, Diary.deleted_at.is_(None))
    )
```

- [ ] **Step 4: Run the integration tests**

```bash
cd apps/api && pytest tests/integration/test_rules_evaluation.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/rules.py apps/api/tests/integration/test_rules_evaluation.py
git commit -m "feat: implement evaluate_event_against_rules with per-series claim support"
```

---

### Task 3: Replace `evaluate_rules_for_event` stub in `tasks.py`

**Files:**
- Modify: `apps/api/app/workers/tasks.py`

- [ ] **Step 1: Replace the stub**

Find the `evaluate_rules_for_event` stub (added in Part 1) and replace its body:

```python
@celery_app.task(name="app.workers.tasks.evaluate_rules_for_event", bind=True, max_retries=3)
def evaluate_rules_for_event(self, event_id: str, diary_id: str) -> None:
    """Evaluate auto-creation rules for a newly ingested event."""
    run_sync(_evaluate_rules_for_event(event_id, diary_id))


async def _evaluate_rules_for_event(event_id: str, diary_id: str) -> None:
    from app.workers.rules import evaluate_event_against_rules
    from app.workers.utils import db_session

    async with db_session() as db:
        await evaluate_event_against_rules(event_id, diary_id, db)
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/workers/tasks.py
git commit -m "feat: wire evaluate_rules_for_event Celery task to rules engine"
```

---

### Task 4: Per-series and tier-limit integration tests

**Files:**
- Create: `apps/api/tests/integration/test_rules_per_series.py`
- Create: `apps/api/tests/integration/test_rule_tier_limit.py`

- [ ] **Step 1: Write per-series test**

Create `apps/api/tests/integration/test_rules_per_series.py`:

```python
"""Test per-series option: 5 recurring instances collapse to 1 entry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry, EntryRuleMatch, RuleSeriesClaim
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_diary, make_event, make_user

SERIES_ID = "google_recurring_xyz"


async def _make_recurring_event(db, diary_id, n: int) -> object:
    dt = datetime(2026, 5, 20, tzinfo=UTC) + timedelta(weeks=n)
    return await make_event(
        db,
        diary_id=diary_id,
        payload={
            "summary": "Weekly standup",
            "description": "",
            "location": "",
            "start": {"dateTime": dt.isoformat()},
            "end": {"dateTime": (dt + timedelta(hours=1)).isoformat()},
            "status": "confirmed",
            "attendees": [],
            "recurringEventId": SERIES_ID,
        },
        occurred_at=dt,
    )


class TestPerSeries:
    async def test_five_instances_produce_one_entry(self, db_session: AsyncSession):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Standup",
            enabled=True,
            condition={"op": "AND", "children": [
                {"field": "title", "op": "contains", "value": "standup", "case_sensitive": False}
            ]},
            options={"recurring": "per_series", "multi_day": "spanning"},
        )
        db_session.add(rule)
        await db_session.commit()

        events = [await _make_recurring_event(db_session, diary.id, i) for i in range(5)]

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            with patch("app.workers.rules.try_enforce_entry_tier_limit", new=AsyncMock(return_value=(True, None))):
                for ev in events:
                    await evaluate_event_against_rules(str(ev.id), str(diary.id), db_session)

        # Only one Entry should exist
        entries = (await db_session.execute(select(Entry).where(Entry.diary_id == diary.id))).scalars().all()
        assert len(entries) == 1

        # One series claim
        claims = (await db_session.execute(select(RuleSeriesClaim).where(RuleSeriesClaim.rule_id == rule.id))).scalars().all()
        assert len(claims) == 1
        assert claims[0].recurring_event_id == SERIES_ID

        # All 5 events attached to the same entry
        for ev in events:
            await db_session.refresh(ev)
            assert ev.entry_id == entries[0].id

        # 5 EntryRuleMatch rows for the same (entry, rule) pair — or 1 if upserted
        # Actually we insert one per evaluation. Use INSERT OR IGNORE to avoid duplicates.
        matches = (await db_session.execute(
            select(EntryRuleMatch).where(EntryRuleMatch.rule_id == rule.id)
        )).scalars().all()
        assert len(matches) >= 1  # at least first match
```

**Note:** The `EntryRuleMatch` has `(entry_id, rule_id)` as PK, so attempting to insert a duplicate will fail. Update `evaluate_event_against_rules` to use `INSERT ... ON CONFLICT DO NOTHING` for the `EntryRuleMatch` insert when attaching additional events to an existing entry. Replace the `EntryRuleMatch` insert block in `rules.py`:

```python
        # Record rule match (use ON CONFLICT DO NOTHING — same entry can be matched multiple times by same rule)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models import EntryRuleMatch as _ERM

        await db.execute(
            pg_insert(_ERM)
            .values(entry_id=target_entry_id, rule_id=rule.id)
            .on_conflict_do_nothing()
        )
```

- [ ] **Step 2: Write tier limit test**

Create `apps/api/tests/integration/test_rule_tier_limit.py`:

```python
"""Test that rule-created entries respect the free-tier auto limit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user


class TestRuleTierLimit:
    async def test_fourth_auto_entry_not_created_on_free_tier(self, db_session: AsyncSession):
        user = await make_user(db_session, subscription_tier="free")
        diary = await make_diary(db_session, owner=user)

        # Seed 3 existing auto entries (free tier limit is 3)
        for i in range(3):
            e = Entry(
                diary_id=diary.id,
                entry_date=datetime(2026, 5, i + 1).date(),
                status="draft",
                created_by="auto",
                creation_source="rule",
            )
            db_session.add(e)
        await db_session.commit()

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Soccer",
            enabled=True,
            condition={"op": "AND", "children": [
                {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
            ]},
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )
        db_session.add(rule)
        await db_session.commit()

        ev = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                "summary": "Soccer practice",
                "description": "",
                "location": "",
                "start": {"dateTime": "2026-05-20T10:00:00Z"},
                "end": {},
                "status": "confirmed",
                "attendees": [],
                "recurringEventId": None,
            },
        )

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            await evaluate_event_against_rules(str(ev.id), str(diary.id), db_session)

        # 4th entry must NOT be created
        all_entries = (
            await db_session.execute(select(Entry).where(Entry.diary_id == diary.id))
        ).scalars().all()
        assert len(all_entries) == 3

        # Event must remain unattached
        await db_session.refresh(ev)
        assert ev.entry_id is None
```

- [ ] **Step 3: Run both tests**

```bash
cd apps/api && pytest tests/integration/test_rules_per_series.py tests/integration/test_rule_tier_limit.py -v
```

Expected: both PASS after the `on_conflict_do_nothing` fix is applied.

- [ ] **Step 4: Commit**

```bash
git add \
  apps/api/app/workers/rules.py \
  apps/api/tests/integration/test_rules_per_series.py \
  apps/api/tests/integration/test_rule_tier_limit.py
git commit -m "test: add per-series and tier-limit integration tests for rules engine"
```

---

### Task 5: Rule backfill task

**Files:**
- Modify: `apps/api/app/workers/tasks.py`
- Create: `apps/api/tests/integration/test_rule_backfill.py`

- [ ] **Step 1: Write the backfill test**

Create `apps/api/tests/integration/test_rule_backfill.py`:

```python
"""Integration test for apply_rule_backfill task."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry, EntryRuleMatch
from app.workers.tasks import _apply_rule_backfill
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user


class TestApplyRuleBackfill:
    async def test_creates_entries_for_unattached_matching_events(
        self, db_session: AsyncSession
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Soccer",
            enabled=True,
            condition={"op": "AND", "children": [
                {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
            ]},
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )
        db_session.add(rule)
        await db_session.commit()

        ev = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                "summary": "Soccer practice",
                "description": "",
                "location": "",
                "start": {"dateTime": "2026-05-20T10:00:00Z"},
                "end": {},
                "status": "confirmed",
                "attendees": [],
                "recurringEventId": None,
            },
            occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            with patch("app.workers.rules.try_enforce_entry_tier_limit", new=AsyncMock(return_value=(True, None))):
                await _apply_rule_backfill(str(rule.id), 30)

        await db_session.refresh(ev)
        assert ev.entry_id is not None

    async def test_adds_match_rows_for_already_attached_matching_events(
        self, db_session: AsyncSession
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="Soccer",
            enabled=True,
            condition={"op": "AND", "children": [
                {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
            ]},
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )
        db_session.add(rule)

        entry = await make_entry(db_session, diary, entry_date=datetime(2026, 5, 20).date())

        ev = await make_event(
            db_session,
            diary_id=diary.id,
            entry=entry,
            payload={
                "summary": "Soccer practice",
                "description": "",
                "location": "",
                "start": {"dateTime": "2026-05-20T10:00:00Z"},
                "end": {},
                "status": "confirmed",
                "attendees": [],
                "recurringEventId": None,
            },
            occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )
        await db_session.commit()

        with patch("app.workers.rules.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            with patch("app.workers.rules.try_enforce_entry_tier_limit", new=AsyncMock(return_value=(True, None))):
                await _apply_rule_backfill(str(rule.id), 30)

        matches = (await db_session.execute(
            select(EntryRuleMatch).where(EntryRuleMatch.rule_id == rule.id)
        )).scalars().all()
        assert len(matches) >= 1
        assert matches[0].entry_id == entry.id
```

- [ ] **Step 2: Add `apply_rule_backfill` task to `tasks.py`**

```python
@celery_app.task(name="app.workers.tasks.apply_rule_backfill", bind=True, max_retries=3)
def apply_rule_backfill(self, rule_id: str, days: int) -> None:
    """Apply an auto-creation rule retroactively to events from the last N days."""
    run_sync(_apply_rule_backfill(rule_id, days))


async def _apply_rule_backfill(rule_id: str, days: int) -> None:
    from datetime import timedelta

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import AutoCreationRule, Diary, Entry, EntryRuleMatch, Event
    from app.workers.rules import evaluate_event_against_rules, match_event
    from app.workers.utils import db_session

    rule_uuid = uuid.UUID(rule_id)
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    async with db_session() as db:
        rule_result = await db.execute(
            select(AutoCreationRule).where(AutoCreationRule.id == rule_uuid)
        )
        rule = rule_result.scalar_one_or_none()
        if rule is None:
            return

        # Evaluate against unattached events first
        unattached_result = await db.execute(
            select(Event).where(
                Event.diary_id == rule.diary_id,
                Event.entry_id.is_(None),
                Event.occurred_at >= cutoff,
            )
        )
        for event in unattached_result.scalars():
            if match_event(rule.condition, event.payload or {}):
                await evaluate_event_against_rules(str(event.id), str(rule.diary_id), db)

        # For already-attached events that match: just add match row
        attached_result = await db.execute(
            select(Event).where(
                Event.diary_id == rule.diary_id,
                Event.entry_id.is_not(None),
                Event.occurred_at >= cutoff,
            )
        )
        for event in attached_result.scalars():
            if match_event(rule.condition, event.payload or {}):
                await db.execute(
                    pg_insert(EntryRuleMatch)
                    .values(entry_id=event.entry_id, rule_id=rule_uuid)
                    .on_conflict_do_nothing()
                )

        from datetime import UTC, datetime
        rule.last_applied_at = datetime.now(tz=UTC)
```

- [ ] **Step 3: Run the backfill tests**

```bash
cd apps/api && pytest tests/integration/test_rule_backfill.py -v
```

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workers/tasks.py apps/api/tests/integration/test_rule_backfill.py
git commit -m "feat: add apply_rule_backfill Celery task with integration tests"
```

---

### Task 6: Rules CRUD + preview + apply endpoints

**Files:**
- Create: `apps/api/app/routers/v1/rules.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/integration/test_rules_endpoint.py`

- [ ] **Step 1: Write the endpoint tests**

Create `apps/api/tests/integration/test_rules_endpoint.py`:

```python
"""Integration tests for rules CRUD, preview, and apply endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.factories import make_diary, make_event, make_user


async def _setup(client, email: str):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, auth, diary


SIMPLE_CONDITION = {
    "op": "AND",
    "children": [
        {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
    ],
}
SIMPLE_OPTIONS = {"recurring": "per_instance", "multi_day": "spanning"}


class TestRulesCRUD:
    async def test_create_and_list_rule(self, client: AsyncClient):
        _, auth, diary = await _setup(client, "rules-crud@example.com")

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules",
            json={"name": "Soccer", "condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
            headers=auth,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Soccer"
        assert data["enabled"] is True

        list_r = await client.get(f"/v1/diaries/{diary['id']}/rules", headers=auth)
        assert list_r.status_code == 200
        assert len(list_r.json()) == 1

    async def test_patch_rule(self, client: AsyncClient):
        _, auth, diary = await _setup(client, "rules-patch@example.com")
        rule = (
            await client.post(
                f"/v1/diaries/{diary['id']}/rules",
                json={"name": "Soccer", "condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
                headers=auth,
            )
        ).json()

        r = await client.patch(
            f"/v1/rules/{rule['id']}",
            json={"enabled": False},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    async def test_delete_rule(self, client: AsyncClient):
        _, auth, diary = await _setup(client, "rules-delete@example.com")
        rule = (
            await client.post(
                f"/v1/diaries/{diary['id']}/rules",
                json={"name": "Soccer", "condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
                headers=auth,
            )
        ).json()

        r = await client.delete(f"/v1/rules/{rule['id']}", headers=auth)
        assert r.status_code == 204

        list_r = await client.get(f"/v1/diaries/{diary['id']}/rules", headers=auth)
        assert len(list_r.json()) == 0


class TestRulesPreview:
    async def test_preview_returns_match_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        _, auth, diary = await _setup(client, "rules-preview@example.com")
        diary_id = uuid.UUID(diary["id"])

        # Seed 2 matching and 1 non-matching events in the last 90 days
        from datetime import timedelta
        now = datetime.now(tz=UTC)
        for summary in ["Soccer practice", "Soccer game"]:
            await make_event(
                db_session,
                diary_id=diary_id,
                payload={"summary": summary, "description": "", "location": "", "start": {}, "end": {}, "status": "", "attendees": []},
                occurred_at=now - timedelta(days=5),
            )
        await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "Piano lesson", "description": "", "location": "", "start": {}, "end": {}, "status": "", "attendees": []},
            occurred_at=now - timedelta(days=5),
        )

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules/preview",
            json={"condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
            headers=auth,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 2
        assert data["threshold_exceeded"] is False
        assert len(data["sample"]) == 2

    async def test_preview_threshold_exceeded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        _, auth, diary = await _setup(client, "rules-threshold@example.com")
        diary_id = uuid.UUID(diary["id"])

        from datetime import timedelta
        now = datetime.now(tz=UTC)
        for i in range(35):
            await make_event(
                db_session,
                diary_id=diary_id,
                payload={"summary": f"Soccer {i}", "description": "", "location": "", "start": {}, "end": {}, "status": "", "attendees": []},
                occurred_at=now - timedelta(days=i % 89),
            )

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules/preview",
            json={"condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["threshold_exceeded"] is True


class TestRulesApply:
    async def test_apply_queues_backfill(self, client: AsyncClient):
        _, auth, diary = await _setup(client, "rules-apply@example.com")
        rule = (
            await client.post(
                f"/v1/diaries/{diary['id']}/rules",
                json={"name": "Soccer", "condition": SIMPLE_CONDITION, "options": SIMPLE_OPTIONS},
                headers=auth,
            )
        ).json()

        with patch("app.workers.tasks.apply_rule_backfill") as mock_task:
            mock_task.delay = lambda rule_id, days: None
            r = await client.post(
                f"/v1/rules/{rule['id']}/apply",
                json={"days": 30},
                headers=auth,
            )

        assert r.status_code == 200
        assert r.json()["queued"] is True
```

- [ ] **Step 2: Write the rules router**

Create `apps/api/app/routers/v1/rules.py`:

```python
"""CRUD, preview, and apply endpoints for auto-creation rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import AutoCreationRule, Event, User
from app.routers.v1.diaries import _get_diary_or_404

router = APIRouter(tags=["rules"])

PREVIEW_THRESHOLD = 30
PREVIEW_MAX_EVENTS = 5000
PREVIEW_SAMPLE_SIZE = 10


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    name: str
    condition: dict
    options: dict
    enabled: bool = True

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict) -> dict:
        _validate_condition_tree(v, depth=0, leaf_count=[0])
        return v


class RulePatch(BaseModel):
    name: str | None = None
    condition: dict | None = None
    options: dict | None = None
    enabled: bool | None = None

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict | None) -> dict | None:
        if v is not None:
            _validate_condition_tree(v, depth=0, leaf_count=[0])
        return v


class PreviewBody(BaseModel):
    condition: dict
    options: dict

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict) -> dict:
        _validate_condition_tree(v, depth=0, leaf_count=[0])
        return v


class ApplyBody(BaseModel):
    days: int = Query(ge=1, le=365, default=30)


class RuleOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    name: str
    enabled: bool
    condition: dict
    options: dict
    last_applied_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PreviewOut(BaseModel):
    matched_count: int
    sample: list[dict]
    total_evaluated: int
    threshold_exceeded: bool


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_condition_tree(node: dict, depth: int, leaf_count: list[int]) -> None:
    if depth > 5:
        raise ValueError("Condition tree exceeds maximum depth of 5")
    op = node.get("op")
    if op in ("AND", "OR"):
        children = node.get("children") or []
        if len(children) > 20:
            raise ValueError("A group may have at most 20 children")
        for child in children:
            _validate_condition_tree(child, depth + 1, leaf_count)
    else:
        leaf_count[0] += 1
        if leaf_count[0] > 50:
            raise ValueError("Condition tree exceeds maximum of 50 leaves")
        valid_fields = {"title", "description", "location", "attendee_email"}
        valid_ops = {"contains", "equals", "not_contains"}
        if node.get("field") not in valid_fields:
            raise ValueError(f"Invalid field: {node.get('field')!r}")
        if node.get("op") not in valid_ops:
            raise ValueError(f"Invalid operator: {node.get('op')!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_rule_or_404(rule_id: uuid.UUID, user: User, db: AsyncSession) -> tuple[AutoCreationRule, str]:
    result = await db.execute(select(AutoCreationRule).where(AutoCreationRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="not_found")
    _, role = await _get_diary_or_404(rule.diary_id, user, db)
    return rule, role


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/diaries/{diary_id}/rules", response_model=list[RuleOut])
async def list_rules(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RuleOut]:
    await _get_diary_or_404(diary_id, user, db)
    result = await db.execute(
        select(AutoCreationRule)
        .where(AutoCreationRule.diary_id == diary_id)
        .order_by(AutoCreationRule.created_at.asc())
    )
    return [RuleOut.model_validate(r) for r in result.scalars()]


@router.post("/diaries/{diary_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    diary_id: uuid.UUID,
    body: RuleCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    _, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")
    rule = AutoCreationRule(
        diary_id=diary_id,
        name=body.name,
        enabled=body.enabled,
        condition=body.condition,
        options=body.options,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return RuleOut.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def patch_rule(
    rule_id: uuid.UUID,
    body: RulePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return RuleOut.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")
    await db.delete(rule)


@router.post("/diaries/{diary_id}/rules/preview", response_model=PreviewOut)
async def preview_rule(
    diary_id: uuid.UUID,
    body: PreviewBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewOut:
    await _get_diary_or_404(diary_id, user, db)

    cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    result = await db.execute(
        select(Event)
        .where(Event.diary_id == diary_id, Event.occurred_at >= cutoff)
        .order_by(Event.occurred_at.desc())
        .limit(PREVIEW_MAX_EVENTS)
    )
    events = result.scalars().all()

    from app.workers.rules import match_event

    matched = [e for e in events if match_event(body.condition, e.payload or {})]

    sample = []
    for ev in matched[:PREVIEW_SAMPLE_SIZE]:
        p = ev.payload or {}
        sample.append({
            "id": str(ev.id),
            "summary": p.get("summary", ""),
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            "location": p.get("location", ""),
        })

    return PreviewOut(
        matched_count=len(matched),
        sample=sample,
        total_evaluated=len(events),
        threshold_exceeded=len(matched) > PREVIEW_THRESHOLD,
    )


@router.post("/rules/{rule_id}/apply")
async def apply_rule(
    rule_id: uuid.UUID,
    body: ApplyBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")
    from app.workers.tasks import apply_rule_backfill
    apply_rule_backfill.delay(str(rule.id), body.days)
    return {"queued": True}
```

- [ ] **Step 3: Register the router in `main.py`**

```python
    from app.routers.v1 import auth, calendar_events, diaries, entries, integrations, rules, scan

    app.include_router(auth.router, prefix="/v1")
    app.include_router(diaries.router, prefix="/v1")
    app.include_router(entries.router, prefix="/v1")
    app.include_router(calendar_events.router, prefix="/v1")
    app.include_router(integrations.router, prefix="/v1")
    app.include_router(rules.router, prefix="/v1")
    app.include_router(scan.router, prefix="/v1")
```

- [ ] **Step 4: Run the endpoint tests**

```bash
cd apps/api && pytest tests/integration/test_rules_endpoint.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add \
  apps/api/app/routers/v1/rules.py \
  apps/api/app/main.py \
  apps/api/tests/integration/test_rules_endpoint.py
git commit -m "feat: add rules CRUD, preview, and apply endpoints"
```

---

### Task 7: Add `rule_matches` to `EntryOut`

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py`

- [ ] **Step 1: Add `RuleMatchOut` and extend `EntryOut`**

In `apps/api/app/routers/v1/entries.py`:

Add after the `EventOut` class:

```python
class RuleMatchOut(BaseModel):
    rule_id: uuid.UUID
    rule_name: str
    matched_at: datetime

    model_config = {"from_attributes": False}
```

Add `rule_matches` field to `EntryOut`:

```python
class EntryOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    entry_date: date
    entry_end_date: date | None
    title: str | None
    body_markdown: str | None
    flagged_tokens: list[str] | None
    status: str
    created_by: str
    creation_source: str = "manual"
    published_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    body_source: str = "llm"
    events: list[EventOut] = []
    rule_matches: list[RuleMatchOut] = []

    model_config = {"from_attributes": True}
```

Update `_get_entry_or_404` to also load `rule_matches` via selectinload:

```python
async def _get_entry_or_404(
    entry_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    require_editor: bool = False,
) -> tuple[Entry, Diary, str | None]:
    from app.models import EntryRuleMatch, AutoCreationRule
    result = await db.execute(
        select(Entry)
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
        )
        .where(Entry.id == entry_id, Entry.deleted_at.is_(None))
    )
    ...
```

Update `_entry_out_from_orm` to populate `rule_matches`:

```python
def _entry_out_from_orm(entry: Entry) -> EntryOut:
    events_out = sorted(
        [_event_out_from_orm(e) for e in entry.events],
        key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=UTC),
    )
    rule_matches_out = [
        RuleMatchOut(
            rule_id=rm.rule_id,
            rule_name=rm.rule.name if rm.rule else "Unknown rule",
            matched_at=rm.matched_at,
        )
        for rm in (entry.rule_matches or [])
    ]
    return EntryOut(
        id=entry.id,
        diary_id=entry.diary_id,
        entry_date=entry.entry_date,
        entry_end_date=entry.entry_end_date,
        title=entry.title,
        body_markdown=entry.body_markdown,
        flagged_tokens=entry.flagged_tokens,
        status=entry.status,
        created_by=entry.created_by,
        creation_source=entry.creation_source,
        published_at=entry.published_at,
        deleted_at=entry.deleted_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        body_source=entry.body_source,
        events=events_out,
        rule_matches=rule_matches_out,
    )
```

Note: `entry.rule_matches` won't be loaded unless `selectinload` is applied. The list and get endpoints use different paths — update the list endpoint too by adding `selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule)` to the `q` query in `list_entries`.

You'll also need to add `from app.models import EntryRuleMatch, AutoCreationRule` to the imports at the top of `entries.py`.

- [ ] **Step 2: Run all tests**

```bash
cd apps/api && make test
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/routers/v1/entries.py
git commit -m "feat: expose rule_matches on EntryOut so UI can show 'captured by rule' badge"
```

---

### Task 8: Frontend — API types and client

**Files:**
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: Add `RuleMatchSummary`, `Rule`, `RulePreview` types and update `Entry`**

In `apps/web/src/lib/api.ts`, add after `CalendarEventSummary`:

```typescript
export interface RuleMatchSummary {
  rule_id: string
  rule_name: string
  matched_at: string
}

export interface Rule {
  id: string
  diary_id: string
  name: string
  enabled: boolean
  condition: ConditionNode
  options: RuleOptions
  last_applied_at: string | null
  created_at: string
  updated_at: string
}

export type ConditionNode = ConditionGroup | ConditionLeaf

export interface ConditionGroup {
  op: 'AND' | 'OR'
  children: ConditionNode[]
}

export interface ConditionLeaf {
  field: 'title' | 'description' | 'location' | 'attendee_email'
  op: 'contains' | 'equals' | 'not_contains'
  value: string
  case_sensitive: boolean
}

export interface RuleOptions {
  recurring: 'per_instance' | 'per_series'
  multi_day: 'per_day' | 'spanning'
}

export interface RulePreview {
  matched_count: number
  sample: Array<{ id: string; summary: string; occurred_at: string | null; location: string }>
  total_evaluated: number
  threshold_exceeded: boolean
}
```

Add `rule_matches` to the `Entry` interface:

```typescript
  rule_matches: RuleMatchSummary[]
```

- [ ] **Step 2: Add `rules` namespace to the `api` object**

```typescript
  rules: {
    async list(diaryId: string): Promise<Rule[]> {
      return apiFetch(`/v1/diaries/${diaryId}/rules`)
    },
    async create(diaryId: string, data: { name: string; condition: ConditionNode; options: RuleOptions; enabled?: boolean }): Promise<Rule> {
      return apiFetch(`/v1/diaries/${diaryId}/rules`, { method: 'POST', body: JSON.stringify(data) })
    },
    async patch(ruleId: string, data: Partial<Pick<Rule, 'name' | 'condition' | 'options' | 'enabled'>>): Promise<Rule> {
      return apiFetch(`/v1/rules/${ruleId}`, { method: 'PATCH', body: JSON.stringify(data) })
    },
    async delete(ruleId: string): Promise<void> {
      return apiFetch(`/v1/rules/${ruleId}`, { method: 'DELETE' })
    },
    async preview(diaryId: string, condition: ConditionNode, options: RuleOptions): Promise<RulePreview> {
      return apiFetch(`/v1/diaries/${diaryId}/rules/preview`, {
        method: 'POST',
        body: JSON.stringify({ condition, options }),
      })
    },
    async apply(ruleId: string, days: number): Promise<{ queued: boolean }> {
      return apiFetch(`/v1/rules/${ruleId}/apply`, { method: 'POST', body: JSON.stringify({ days }) })
    },
  },
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/api.ts
git commit -m "feat: add Rule types and rules API client namespace"
```

---

### Task 9: Frontend — Rules list page

**Files:**
- Create: `apps/web/src/app/diaries/[diaryId]/rules/page.tsx`

- [ ] **Step 1: Write the rules list page**

```tsx
'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Rule } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

export default function RulesListPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [rules, setRules] = useState<Rule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [applyingRule, setApplyingRule] = useState<string | null>(null)
  const [applyDays, setApplyDays] = useState<Record<string, number>>({})

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.rules.list(diaryId)
      .then(setRules)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load rules'))
      .finally(() => setLoading(false))
  }, [user, diaryId])

  async function handleToggle(rule: Rule) {
    try {
      const updated = await api.rules.patch(rule.id, { enabled: !rule.enabled })
      setRules((prev) => prev.map((r) => (r.id === rule.id ? updated : r)))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to update rule')
    }
  }

  async function handleDelete(rule: Rule) {
    if (!confirm(`Delete rule "${rule.name}"? This will not remove entries that were already created by this rule.`)) return
    try {
      await api.rules.delete(rule.id)
      setRules((prev) => prev.filter((r) => r.id !== rule.id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete rule')
    }
  }

  async function handleApply(rule: Rule) {
    const days = applyDays[rule.id] ?? 30
    setApplyingRule(rule.id)
    try {
      await api.rules.apply(rule.id, days)
      alert(`Rule "${rule.name}" has been queued to apply to events from the last ${days} days.`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to apply rule')
    } finally {
      setApplyingRule(null)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <div className="page-header">
          <h1 className="page-title">Auto-Creation Rules</h1>
          <div className="page-actions">
            <Link href={`/diaries/${diaryId}/rules/new`} className="btn btn-primary">
              + New rule
            </Link>
          </div>
        </div>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Rules automatically create diary entries from calendar events that match your criteria.
        </p>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {rules.length === 0 ? (
          <div className="empty-state">
            <p>No rules yet. Create a rule to automatically generate entries from matching calendar events.</p>
          </div>
        ) : (
          rules.map((rule) => (
            <div key={rule.id} className="card" style={{ marginBottom: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: '1rem' }}>{rule.name}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    {rule.last_applied_at
                      ? `Last applied: ${new Date(rule.last_applied_at).toLocaleDateString()}`
                      : 'Never applied'}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.875rem', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={() => handleToggle(rule)}
                    />
                    Enabled
                  </label>
                  <Link href={`/rules/${rule.id}`} className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}>
                    Edit
                  </Link>
                  <button
                    className="btn btn-danger"
                    style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}
                    onClick={() => handleDelete(rule)}
                  >
                    Delete
                  </button>
                </div>
              </div>
              <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Apply to past:</span>
                <select
                  value={applyDays[rule.id] ?? 30}
                  onChange={(e) => setApplyDays((prev) => ({ ...prev, [rule.id]: Number(e.target.value) }))}
                  style={{ fontSize: '0.8rem', padding: '0.2rem 0.4rem' }}
                >
                  {[1, 7, 30, 90].map((d) => (
                    <option key={d} value={d}>{d} days</option>
                  ))}
                </select>
                <button
                  className="btn btn-secondary"
                  style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}
                  onClick={() => handleApply(rule)}
                  disabled={applyingRule === rule.id}
                >
                  {applyingRule === rule.id ? 'Applying…' : 'Apply'}
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/app/diaries/[diaryId]/rules/page.tsx
git commit -m "feat: add rules list page with toggle, delete, and apply-to-past"
```

---

### Task 10: Frontend — `RuleForm` component and rule create/edit pages

**Files:**
- Create: `apps/web/src/components/RuleForm.tsx`
- Create: `apps/web/src/app/diaries/[diaryId]/rules/new/page.tsx`
- Create: `apps/web/src/app/rules/[ruleId]/page.tsx`

- [ ] **Step 1: Write the shared `RuleForm` component**

Create `apps/web/src/components/RuleForm.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type ConditionGroup,
  type ConditionLeaf,
  type ConditionNode,
  type Rule,
  type RuleOptions,
  type RulePreview,
} from '@/lib/api'

// ---------------------------------------------------------------------------
// Default values
// ---------------------------------------------------------------------------

const DEFAULT_LEAF: ConditionLeaf = {
  field: 'title',
  op: 'contains',
  value: '',
  case_sensitive: false,
}

const DEFAULT_OPTIONS: RuleOptions = { recurring: 'per_instance', multi_day: 'spanning' }

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  diaryId: string
  initialRule?: Rule
  onSave: (rule: Rule) => void
  onCancel: () => void
}

// ---------------------------------------------------------------------------
// ConditionLeafEditor
// ---------------------------------------------------------------------------

function LeafEditor({
  leaf,
  onChange,
  onRemove,
}: {
  leaf: ConditionLeaf
  onChange: (l: ConditionLeaf) => void
  onRemove: () => void
}) {
  return (
    <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '0.4rem' }}>
      <select
        value={leaf.field}
        onChange={(e) => onChange({ ...leaf, field: e.target.value as ConditionLeaf['field'] })}
        style={{ fontSize: '0.85rem' }}
      >
        <option value="title">Title</option>
        <option value="description">Description</option>
        <option value="location">Location</option>
        <option value="attendee_email">Attendee email</option>
      </select>
      <select
        value={leaf.op}
        onChange={(e) => onChange({ ...leaf, op: e.target.value as ConditionLeaf['op'] })}
        style={{ fontSize: '0.85rem' }}
      >
        <option value="contains">contains</option>
        <option value="equals">equals</option>
        <option value="not_contains">does not contain</option>
      </select>
      <input
        type="text"
        value={leaf.value}
        onChange={(e) => onChange({ ...leaf, value: e.target.value })}
        placeholder={
          leaf.field === 'attendee_email'
            ? 'e.g. alice@example.com'
            : leaf.field === 'location'
            ? 'e.g. school, park'
            : 'e.g. soccer, Kay'
        }
        style={{ fontSize: '0.85rem', minWidth: 160 }}
      />
      <button
        type="button"
        onClick={onRemove}
        style={{ fontSize: '0.75rem', color: '#cc0000', background: 'none', border: 'none', cursor: 'pointer' }}
      >
        ✕
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConditionGroupEditor (recursive)
// ---------------------------------------------------------------------------

function GroupEditor({
  node,
  onChange,
  onRemove,
  depth,
}: {
  node: ConditionGroup
  onChange: (n: ConditionGroup) => void
  onRemove?: () => void
  depth: number
}) {
  function updateChild(i: number, child: ConditionNode) {
    const children = [...node.children]
    children[i] = child
    onChange({ ...node, children })
  }

  function removeChild(i: number) {
    const children = node.children.filter((_, idx) => idx !== i)
    onChange({ ...node, children })
  }

  function addLeaf() {
    onChange({ ...node, children: [...node.children, { ...DEFAULT_LEAF }] })
  }

  function addGroup() {
    const newGroup: ConditionGroup = { op: 'AND', children: [{ ...DEFAULT_LEAF }] }
    onChange({ ...node, children: [...node.children, newGroup] })
  }

  return (
    <div style={{
      border: '1px solid #ddd',
      borderRadius: 6,
      padding: '0.75rem',
      marginBottom: '0.5rem',
      background: depth === 0 ? '#fafafa' : '#f4f4f4',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <select
          value={node.op}
          onChange={(e) => onChange({ ...node, op: e.target.value as 'AND' | 'OR' })}
          style={{ fontWeight: 600, fontSize: '0.85rem' }}
        >
          <option value="AND">AND (all must match)</option>
          <option value="OR">OR (any must match)</option>
        </select>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            style={{ fontSize: '0.75rem', color: '#cc0000', background: 'none', border: 'none', cursor: 'pointer', marginLeft: 'auto' }}
          >
            Remove group
          </button>
        )}
      </div>

      {node.children.map((child, i) =>
        'children' in child ? (
          <GroupEditor
            key={i}
            node={child as ConditionGroup}
            onChange={(updated) => updateChild(i, updated)}
            onRemove={() => removeChild(i)}
            depth={depth + 1}
          />
        ) : (
          <LeafEditor
            key={i}
            leaf={child as ConditionLeaf}
            onChange={(updated) => updateChild(i, updated)}
            onRemove={() => removeChild(i)}
          />
        )
      )}

      <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.4rem' }}>
        <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem' }} onClick={addLeaf}>
          + Condition
        </button>
        {depth < 4 && (
          <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem' }} onClick={addGroup}>
            + Group
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// RuleForm
// ---------------------------------------------------------------------------

export default function RuleForm({ diaryId, initialRule, onSave, onCancel }: Props) {
  const [name, setName] = useState(initialRule?.name ?? '')
  const [condition, setCondition] = useState<ConditionGroup>(
    (initialRule?.condition as ConditionGroup | undefined) ?? { op: 'AND', children: [{ ...DEFAULT_LEAF }] }
  )
  const [options, setOptions] = useState<RuleOptions>(initialRule?.options ?? DEFAULT_OPTIONS)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<RulePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchPreview = useCallback(async (cond: ConditionGroup, opts: RuleOptions) => {
    setPreviewLoading(true)
    try {
      const result = await api.rules.preview(diaryId, cond, opts)
      setPreview(result)
    } catch {
      setPreview(null)
    } finally {
      setPreviewLoading(false)
    }
  }, [diaryId])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchPreview(condition, options)
    }, 500)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [condition, options, fetchPreview])

  async function handleSave() {
    if (!name.trim()) {
      setError('Rule name is required.')
      return
    }
    setSaving(true)
    setError('')
    try {
      let rule: Rule
      if (initialRule) {
        rule = await api.rules.patch(initialRule.id, { name, condition, options })
      } else {
        rule = await api.rules.create(diaryId, { name, condition, options })
      }
      onSave(rule)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save rule')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
      {/* Left: form */}
      <div>
        <div className="form-field" style={{ marginBottom: '1rem' }}>
          <label className="form-label" htmlFor="rule-name">Rule name</label>
          <input
            id="rule-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Soccer activities"
          />
        </div>

        <div style={{ marginBottom: '1rem' }}>
          <div className="form-label" style={{ marginBottom: '0.4rem' }}>Conditions</div>
          <GroupEditor
            node={condition}
            onChange={setCondition}
            depth={0}
          />
        </div>

        <div className="card" style={{ marginBottom: '1rem', padding: '0.75rem' }}>
          <div className="form-label" style={{ marginBottom: '0.4rem' }}>Options</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.875rem' }}>
            <label>
              Recurring events:{' '}
              <select
                value={options.recurring}
                onChange={(e) => setOptions((o) => ({ ...o, recurring: e.target.value as RuleOptions['recurring'] }))}
                style={{ fontSize: '0.85rem' }}
              >
                <option value="per_instance">One entry per occurrence</option>
                <option value="per_series">One entry for the whole series</option>
              </select>
            </label>
            <label>
              Multi-day events:{' '}
              <select
                value={options.multi_day}
                onChange={(e) => setOptions((o) => ({ ...o, multi_day: e.target.value as RuleOptions['multi_day'] }))}
                style={{ fontSize: '0.85rem' }}
              >
                <option value="spanning">One entry spanning all days</option>
                <option value="per_day">One entry per day</option>
              </select>
            </label>
          </div>
        </div>

        {error && <p className="error-message" style={{ marginBottom: '0.75rem' }}>{error}</p>}

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save rule'}
          </button>
          <button className="btn btn-secondary" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>

      {/* Right: live preview */}
      <div>
        <div className="form-label" style={{ marginBottom: '0.4rem' }}>
          Live preview {previewLoading && <span style={{ color: '#888', fontWeight: 400 }}>loading…</span>}
        </div>

        {preview && (
          <>
            {preview.threshold_exceeded && (
              <div style={{
                background: '#fffbeb',
                border: '1px solid #f59e0b',
                borderRadius: 6,
                padding: '0.75rem',
                marginBottom: '0.75rem',
                fontSize: '0.875rem',
                color: '#92400e',
              }}>
                <strong>⚠ High volume warning:</strong> This rule would match ~{preview.matched_count} events from the last 90 days. This will create a lot of entries automatically. Are you sure you want this?
              </div>
            )}

            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
              Matches <strong>{preview.matched_count}</strong> of {preview.total_evaluated} events in the last 90 days
            </div>

            {preview.sample.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {preview.sample.map((ev) => (
                  <li key={ev.id} style={{ padding: '0.3rem 0', borderTop: '1px solid #eee', fontSize: '0.85rem' }}>
                    <strong>{ev.summary || '(no title)'}</strong>
                    {ev.location ? <span style={{ color: '#888' }}> · {ev.location}</span> : null}
                    {ev.occurred_at && (
                      <span style={{ color: '#aaa', display: 'block', fontSize: '0.75rem' }}>
                        {new Date(ev.occurred_at).toLocaleDateString()}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {preview.matched_count === 0 && (
              <div style={{ color: '#888', fontStyle: 'italic', fontSize: '0.85rem' }}>
                No events matched in the last 90 days.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Write the rule create page**

Create `apps/web/src/app/diaries/[diaryId]/rules/new/page.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { useEffect } from 'react'
import RuleForm from '@/components/RuleForm'

export default function NewRulePage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  if (authLoading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}/rules`} className="nav-brand">← Rules</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <h1 className="page-title" style={{ marginBottom: '1.5rem' }}>New auto-creation rule</h1>
        <RuleForm
          diaryId={diaryId}
          onSave={() => router.push(`/diaries/${diaryId}/rules`)}
          onCancel={() => router.push(`/diaries/${diaryId}/rules`)}
        />
      </div>
    </>
  )
}
```

- [ ] **Step 3: Write the rule edit page**

Create `apps/web/src/app/rules/[ruleId]/page.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Rule } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import RuleForm from '@/components/RuleForm'

export default function EditRulePage() {
  const { ruleId } = useParams<{ ruleId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [rule, setRule] = useState<Rule | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !ruleId) return
    // There is no GET /v1/rules/{id} endpoint — fetch the list and find by ID.
    // The rule carries diary_id so we can navigate back.
    // A future improvement would add a single-rule GET endpoint.
    // For now: the diary_id is on the rule object returned from list.
    // We don't know diary_id yet — pass it in the URL as a search param from the list page.
    // Simple alternative: add rule id to the URL and also derive diaryId from the rule.
    // The list page links to /rules/${rule.id}. We need diaryId from somewhere.
    // Solution: store diaryId in URL search params when navigating from the list page,
    // OR add GET /v1/rules/{rule_id} endpoint. For simplicity, add query param support.

    // Read diaryId from URL search params
    const params = new URLSearchParams(window.location.search)
    const diaryId = params.get('diaryId')
    if (!diaryId) {
      setError('Missing diaryId parameter. Return to the diary and try again.')
      setLoading(false)
      return
    }
    api.rules.list(diaryId)
      .then((rules) => {
        const found = rules.find((r) => r.id === ruleId) ?? null
        if (!found) setError('Rule not found.')
        setRule(found)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load rule'))
      .finally(() => setLoading(false))
  }, [user, ruleId, authLoading])

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null
  if (error || !rule) return (
    <div className="container" style={{ paddingTop: '1.5rem' }}>
      <p className="error-message">{error || 'Rule not found.'}</p>
    </div>
  )

  const diaryId = rule.diary_id

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}/rules`} className="nav-brand">← Rules</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <h1 className="page-title" style={{ marginBottom: '1.5rem' }}>Edit rule: {rule.name}</h1>
        <RuleForm
          diaryId={diaryId}
          initialRule={rule}
          onSave={() => router.push(`/diaries/${diaryId}/rules`)}
          onCancel={() => router.push(`/diaries/${diaryId}/rules`)}
        />
      </div>
    </>
  )
}
```

**Update the rules list page to pass `?diaryId` in the edit link.** In `apps/web/src/app/diaries/[diaryId]/rules/page.tsx`, update the Edit link:

```tsx
<Link href={`/rules/${rule.id}?diaryId=${diaryId}`} className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}>
  Edit
</Link>
```

- [ ] **Step 4: Commit**

```bash
git add \
  apps/web/src/components/RuleForm.tsx \
  apps/web/src/app/diaries/[diaryId]/rules/new/page.tsx \
  apps/web/src/app/rules/[ruleId]/page.tsx \
  apps/web/src/app/diaries/[diaryId]/rules/page.tsx
git commit -m "feat: add rule create/edit form with live preview and volume warning"
```

---

### Task 11: Frontend — Entry detail "Captured by rule" badge

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

- [ ] **Step 1: Add rule match badge**

In `apps/web/src/app/entries/[entryId]/page.tsx`, import `Link` if not already imported (it is), then add after the existing fallback notice block (around line 251):

```tsx
            {entry.rule_matches && entry.rule_matches.length > 0 && (
              <div style={{
                fontSize: '0.8rem',
                color: '#555',
                marginBottom: '0.75rem',
                padding: '0.4rem 0.6rem',
                background: '#f0f4ff',
                borderRadius: 4,
                border: '1px solid #c7d7f4',
              }}>
                Captured by rule{entry.rule_matches.length !== 1 ? 's' : ''}:{' '}
                {entry.rule_matches.map((rm, i) => (
                  <span key={rm.rule_id}>
                    {i > 0 ? ', ' : ''}
                    <Link href={`/rules/${rm.rule_id}?diaryId=${entry.diary_id}`} style={{ color: '#3366cc' }}>
                      {rm.rule_name}
                    </Link>
                  </span>
                ))}
              </div>
            )}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/app/entries/[entryId]/page.tsx
git commit -m "feat: show 'Captured by rule(s)' badge on entry detail page"
```

---

### Task 12: Full test suite + final verification

- [ ] **Step 1: Run all backend tests**

```bash
cd apps/api && make test
```

Expected: all unit and integration tests pass.

- [ ] **Step 2: Run full project lint + typecheck + test**

```bash
cd /Users/I549200/Desktop/working/code-projects/personal/perfect-day && make test-all
```

Expected: lint, typecheck, unit, integration, and e2e all pass. Fix any TypeScript type errors (missing `rule_matches` on `Entry` responses in tests, etc.).

- [ ] **Step 3: Manual smoke test**

With the stack running (`make up`):
1. Navigate to a diary, click "Auto-Creation Rules" → empty state page loads.
2. Click "+ New rule" → form loads with AND group and one empty condition.
3. Enter name "Soccer", title contains "soccer" → live preview shows count after 500ms debounce.
4. Save → redirected to rules list, rule visible with enabled toggle.
5. Trigger a scan → any events with "soccer" in the title now become entries.
6. Navigate to an auto-created entry → "Captured by rule: Soccer" badge visible with link back to rule.
7. Enter name for a rule that would match many events (e.g., title contains "e") → yellow volume warning appears.
8. From rules list, choose "90 days" and click Apply → toast confirms queued.
