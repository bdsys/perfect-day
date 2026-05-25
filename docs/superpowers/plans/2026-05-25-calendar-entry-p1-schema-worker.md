# Calendar Entry Refactor — Part 1: Schema + Worker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the scan worker from auto-creating Entry rows; instead store raw Calendar events with `entry_id = NULL`, add `creation_source` to entries, and add new tables needed by the picker and rules engine.

**Architecture:** Alembic migration makes `events.entry_id` nullable and adds three new tables (`auto_creation_rules`, `entry_rule_matches`, `rule_series_claims`) plus a `creation_source` column on `entries`. The SQLAlchemy models are updated to match. The ingest worker is stripped of `_upsert_entry` and the `group_events_into_entries` pass; it now inserts events with `entry_id=NULL`. A no-raise tier-limit helper is added for use by the future rules worker.

**Tech Stack:** Python, SQLAlchemy (async), Alembic, Celery, PostgreSQL, FastAPI, pytest (testcontainers)

---

## File Map

| Action | Path |
|---|---|
| **Create** | `apps/api/alembic/versions/0005_decouple_events_and_rules.py` |
| **Modify** | `apps/api/app/models/__init__.py` |
| **Modify** | `apps/api/app/workers/tasks.py` |
| **Modify** | `apps/api/app/services/tier.py` |
| **Modify** | `apps/api/tests/fixtures/factories.py` |
| **Create** | `apps/api/tests/integration/test_calendar_event_unattached.py` |
| **Modify** | `apps/api/tests/integration/conftest.py` (add new tables to TRUNCATE) |

---

### Task 1: Alembic migration

**Files:**
- Create: `apps/api/alembic/versions/0005_decouple_events_and_rules.py`

- [ ] **Step 1: Write the migration**

```python
"""Decouple events from entries; add creation_source, auto_creation_rules, entry_rule_matches, rule_series_claims

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Make events.entry_id nullable
    op.alter_column("events", "entry_id", nullable=True)

    # 2. Add partial index for fast picker queries (unattached events by date)
    op.create_index(
        "ix_events_unattached_occurred",
        "events",
        ["occurred_at"],
        postgresql_where=sa.text("entry_id IS NULL"),
    )

    # 3. Add creation_source to entries
    op.add_column(
        "entries",
        sa.Column("creation_source", sa.String(20), nullable=True),
    )
    # Backfill existing rows
    op.execute(
        "UPDATE entries SET creation_source = 'legacy_auto' WHERE created_by = 'auto'"
    )
    op.execute(
        "UPDATE entries SET creation_source = 'manual' WHERE created_by = 'manual'"
    )
    op.alter_column("entries", "creation_source", nullable=False, server_default="manual")
    op.create_check_constraint(
        "ck_entries_creation_source",
        "entries",
        "creation_source IN ('manual','calendar_pick','rule','legacy_auto')",
    )

    # 4. New table: auto_creation_rules
    op.create_table(
        "auto_creation_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "diary_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("condition", JSONB, nullable=False),
        sa.Column("options", JSONB, nullable=False, server_default='{"recurring":"per_instance","multi_day":"spanning"}'),
        sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_auto_creation_rules_diary_enabled",
        "auto_creation_rules",
        ["diary_id", "enabled"],
    )

    # 5. New table: entry_rule_matches
    op.create_table(
        "entry_rule_matches",
        sa.Column(
            "entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_entry_rule_matches_rule", "entry_rule_matches", ["rule_id"])

    # 6. New table: rule_series_claims
    op.create_table(
        "rule_series_claims",
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("recurring_event_id", sa.Text, nullable=False, primary_key=True),
        sa.Column(
            "entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # 7. Add rules counters to scan_runs
    op.add_column(
        "scan_runs",
        sa.Column("rules_evaluated", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("rule_matches", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("scan_runs", "rule_matches")
    op.drop_column("scan_runs", "rules_evaluated")
    op.drop_table("rule_series_claims")
    op.drop_index("ix_entry_rule_matches_rule", table_name="entry_rule_matches")
    op.drop_table("entry_rule_matches")
    op.drop_index("ix_auto_creation_rules_diary_enabled", table_name="auto_creation_rules")
    op.drop_table("auto_creation_rules")
    op.drop_constraint("ck_entries_creation_source", "entries", type_="check")
    op.drop_column("entries", "creation_source")
    op.drop_index("ix_events_unattached_occurred", table_name="events")
    op.alter_column("events", "entry_id", nullable=False)
```

- [ ] **Step 2: Run the migration against the dev DB**

```bash
cd apps/api && make migrate
```

Expected: `Running upgrade 0004 -> 0005` with no errors.

- [ ] **Step 3: Verify migration is reversible**

```bash
cd apps/api && alembic downgrade -1 && alembic upgrade head
```

Expected: both commands succeed with no errors.

---

### Task 2: SQLAlchemy model updates

**Files:**
- Modify: `apps/api/app/models/__init__.py`

- [ ] **Step 1: Make `Event.entry_id` nullable and add unattached relationship to Diary**

In `models/__init__.py`, replace the `Event` class (currently lines 312–338):

```python
class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    entry: Mapped[Entry | None] = relationship(back_populates="events")

    __table_args__ = (
        Index(
            "ix_events_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "ix_events_unattached_occurred",
            "occurred_at",
            postgresql_where=text("entry_id IS NULL"),
        ),
        CheckConstraint(
            "source IN ('google_calendar','google_photos','manual','spotify')",
            name="ck_events_source",
        ),
    )
```

- [ ] **Step 2: Add `creation_source` to `Entry`**

In the `Entry` class, add after `created_by`:

```python
    creation_source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="manual"
    )
```

Update `__table_args__` to add the new check constraint and drop the old `ck_entries_created_by` — wait, `ck_entries_created_by` is still valid. Add the new constraint alongside it:

```python
    __table_args__ = (
        Index("ix_entries_diary_entry_date", "diary_id", "entry_date"),
        CheckConstraint("status IN ('draft','published')", name="ck_entries_status"),
        CheckConstraint("created_by IN ('auto','manual')", name="ck_entries_created_by"),
        CheckConstraint("body_source IN ('llm','fallback')", name="ck_entries_body_source"),
        CheckConstraint(
            "creation_source IN ('manual','calendar_pick','rule','legacy_auto')",
            name="ck_entries_creation_source",
        ),
    )
```

Also update `Entry.events` relationship — the back_populates still works but `cascade="all, delete-orphan"` needs to stay since entries can still own events once attached:

```python
    events: Mapped[list[Event]] = relationship(
        back_populates="entry", cascade="all, delete-orphan",
        foreign_keys="Event.entry_id",
    )
```

- [ ] **Step 3: Add three new model classes**

Append after `DiaryCalendarFilter` (before the Notifications section):

```python
# ---------------------------------------------------------------------------
# Auto-creation rules
# ---------------------------------------------------------------------------


class AutoCreationRule(TimestampMixin, Base):
    __tablename__ = "auto_creation_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # condition: AND/OR tree — see docs/superpowers/plans for JSON shape
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # options keys: recurring ('per_instance'|'per_series'), multi_day ('per_day'|'spanning')
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    diary: Mapped[Diary] = relationship(back_populates="auto_creation_rules")
    rule_matches: Mapped[list[EntryRuleMatch]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    series_claims: Mapped[list[RuleSeriesClaim]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_auto_creation_rules_diary_enabled", "diary_id", "enabled"),
    )


class EntryRuleMatch(Base):
    __tablename__ = "entry_rule_matches"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entry: Mapped[Entry] = relationship(back_populates="rule_matches")
    rule: Mapped[AutoCreationRule] = relationship(back_populates="rule_matches")

    __table_args__ = (Index("ix_entry_rule_matches_rule", "rule_id"),)


class RuleSeriesClaim(Base):
    __tablename__ = "rule_series_claims"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    recurring_event_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rule: Mapped[AutoCreationRule] = relationship(back_populates="series_claims")
```

- [ ] **Step 4: Wire back-references**

In `Entry`, add these relationships (after `edit_diffs`):

```python
    rule_matches: Mapped[list[EntryRuleMatch]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
```

In `Diary`, add (after `backfill_runs`):

```python
    auto_creation_rules: Mapped[list[AutoCreationRule]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
```

- [ ] **Step 5: Update `ScanRun` to include the two new counters**

In `ScanRun`, add after `llm_calls_made`:

```python
    rules_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rule_matches: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
```

- [ ] **Step 6: Fix broken `make_event` factory (bug: passes diary_id which Event doesn't have)**

In `apps/api/tests/fixtures/factories.py`, replace the `make_event` function:

```python
async def make_event(
    db: AsyncSession,
    diary_id: uuid.UUID,
    entry: Entry | None = None,
    source: str = "google_calendar",
    external_id: str | None = None,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    ev = Event(
        entry_id=entry.id if entry is not None else None,
        source=source,
        external_id=external_id or uuid.uuid4().hex,
        payload=payload or {"summary": "Test event", "location": "", "description": "", "start": {}, "end": {}, "status": "", "attendees": []},
        occurred_at=occurred_at or datetime.now(tz=UTC),
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev
```

Note: `diary_id` is now passed explicitly so callers can create unattached events without an entry. The `Event` ORM has no `diary_id` field; the argument is accepted but not stored — it's only present to make call sites self-documenting. Actually, `Event` has no `diary_id` column at all. Remove it from the signature and just make `entry` optional:

```python
async def make_event(
    db: AsyncSession,
    entry: Entry | None = None,
    source: str = "google_calendar",
    external_id: str | None = None,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    ev = Event(
        entry_id=entry.id if entry is not None else None,
        source=source,
        external_id=external_id or uuid.uuid4().hex,
        payload=payload
            or {
                "summary": "Test event",
                "location": "",
                "description": "",
                "start": {},
                "end": {},
                "status": "",
                "attendees": [],
            },
        occurred_at=occurred_at or datetime.now(tz=UTC),
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev
```

Update every call site in existing tests that used the old `make_event(db, entry, ...)` signature — search for `make_event` in `apps/api/tests/` and verify no test passes `diary_id` as a positional arg.

- [ ] **Step 7: Update conftest.py TRUNCATE to include new tables**

In `apps/api/tests/integration/conftest.py`, replace the TRUNCATE statement:

```python
        conn.execute(
            sa.text(
                "TRUNCATE TABLE users, diaries, entries, events, scan_jobs, "
                "oauth_tokens, refresh_tokens, audit_log, llm_generations, "
                "entry_edit_diffs, diary_permissions, invitations, scan_runs, "
                "backfill_runs, diary_calendar_filters, notification_preferences, "
                "notifications, auto_creation_rules, entry_rule_matches, rule_series_claims "
                "RESTART IDENTITY CASCADE"
            )
        )
```

---

### Task 3: Write the failing integration test (TDD)

**Files:**
- Create: `apps/api/tests/integration/test_calendar_event_unattached.py`

- [ ] **Step 1: Write the test before changing the worker**

```python
"""Integration test: ingest_calendar_event stores Event with entry_id=NULL after the refactor."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event
from app.workers.tasks import _ingest_calendar_event


@pytest.fixture()
def sample_event_data() -> dict:
    return {
        "id": "abc123",
        "summary": "Soccer practice",
        "description": "At the park",
        "location": "City Park",
        "status": "confirmed",
        "start": {"dateTime": "2026-05-25T10:00:00-05:00"},
        "end": {"dateTime": "2026-05-25T11:30:00-05:00"},
        "attendees": [{"displayName": "Alice", "email": "alice@example.com", "responseStatus": "accepted"}],
    }


class TestIngestCalendarEventUnattached:
    async def test_new_event_has_null_entry_id(self, db_session: AsyncSession, sample_event_data):
        """After refactor, ingest_calendar_event must NOT create an Entry."""
        import uuid
        diary_id = uuid.uuid4()

        with patch("app.workers.tasks.evaluate_rules_for_event") as mock_rules:
            mock_rules.delay = lambda *a, **k: None
            await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")

        result = await db_session.execute(select(Event).where(Event.external_id == "abc123"))
        event = result.scalar_one_or_none()
        assert event is not None, "Event row must be created"
        assert event.entry_id is None, "entry_id must be NULL — no Entry auto-created"
        assert event.payload["summary"] == "Soccer practice"

    async def test_duplicate_event_updates_payload(self, db_session: AsyncSession, sample_event_data):
        """Re-ingesting the same external_id updates payload but keeps entry_id unchanged."""
        import uuid
        diary_id = uuid.uuid4()

        with patch("app.workers.tasks.evaluate_rules_for_event") as mock_rules:
            mock_rules.delay = lambda *a, **k: None
            await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")
            sample_event_data["summary"] = "Soccer practice UPDATED"
            await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")

        result = await db_session.execute(select(Event).where(Event.external_id == "abc123"))
        events = result.scalars().all()
        assert len(events) == 1, "Must not create duplicate Event rows"
        assert events[0].payload["summary"] == "Soccer practice UPDATED"
        assert events[0].entry_id is None
```

- [ ] **Step 2: Run the test and confirm it fails (not yet refactored)**

```bash
cd apps/api && pytest tests/integration/test_calendar_event_unattached.py -v
```

Expected: FAIL — `entry_id is None` assertion fails because the worker still calls `_upsert_entry`.

---

### Task 4: Refactor the ingest worker

**Files:**
- Modify: `apps/api/app/workers/tasks.py`

- [ ] **Step 1: Remove `_upsert_entry` and the grouping pass**

Delete the following from `tasks.py`:
1. The entire `_upsert_entry` async function (lines ~330–384).
2. The `group_events_into_entries` Celery task (lines ~392–394).
3. The `_group_events_into_entries_task` async function (lines ~397–402).
4. The `group_events_into_entries_async` async function (lines ~405–430).

- [ ] **Step 2: Rewrite `_ingest_calendar_event`**

Replace the entire `_ingest_calendar_event` function:

```python
async def _ingest_calendar_event(
    event_data: dict, diary_id: uuid.UUID, diary_timezone: str
) -> str | None:
    """Ingest one Google Calendar event; store with entry_id=NULL.

    Returns the event's UUID string, or None if the date could not be parsed.
    Rule evaluation is queued separately so the worker returns quickly.
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
            select(Event).where(Event.source == source, Event.external_id == external_id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            existing.payload = payload
            return str(existing.id)

        event = Event(
            entry_id=None,
            source=source,
            external_id=external_id,
            occurred_at=occurred_at,
            payload=payload,
        )
        db.add(event)
        await db.flush()
        event_id = event.id

    # Queue rule evaluation (no-op until rules worker exists in Part 3)
    evaluate_rules_for_event.delay(str(event_id), str(diary_id))
    return str(event_id)
```

- [ ] **Step 3: Add the stub `evaluate_rules_for_event` task (to be replaced in Part 3)**

Add after `_ingest_calendar_event`:

```python
@celery_app.task(name="app.workers.tasks.evaluate_rules_for_event", bind=True, max_retries=3)
def evaluate_rules_for_event(self, event_id: str, diary_id: str) -> None:
    """Evaluate auto-creation rules against a newly ingested event.

    This stub does nothing; the real implementation is added in the rules worker module.
    Replace the body in Part 3 of the implementation plan.
    """
    pass
```

- [ ] **Step 4: Update `_scan_diary` to remove the grouping call**

In `_scan_diary`, remove these lines (currently ~lines 144–150):

```python
        # Group events into entries and queue LLM generation
        new_entry_ids: list[uuid.UUID] = []
        async with db_session() as db:
            new_entry_ids = await group_events_into_entries_async(diary_id, scan_run_id, db)

        for entry_id in new_entry_ids:
            generate_entry_draft.delay(str(entry_id))
```

Replace with nothing — the grouping pass is gone. The scan run close block (lines ~153–173) now sets `scan_run_update.entries_created = 0` (rules engine will handle this in Part 3). Leave the `llm_calls_made` counter at 0 too.

The close block should now read:

```python
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
```

- [ ] **Step 5: Run the failing test to confirm it now passes**

```bash
cd apps/api && pytest tests/integration/test_calendar_event_unattached.py -v
```

Expected: PASS — both tests green.

- [ ] **Step 6: Commit**

```bash
git add \
  apps/api/alembic/versions/0005_decouple_events_and_rules.py \
  apps/api/app/models/__init__.py \
  apps/api/app/workers/tasks.py \
  apps/api/tests/fixtures/factories.py \
  apps/api/tests/integration/conftest.py \
  apps/api/tests/integration/test_calendar_event_unattached.py
git commit -m "feat: decouple events from entries; scan stores events with entry_id=NULL"
```

---

### Task 5: Add `try_enforce_entry_tier_limit` to tier service

**Files:**
- Modify: `apps/api/app/services/tier.py`

The existing `enforce_entry_tier_limit` raises `HTTPException`. Celery workers must not propagate HTTP exceptions. Add a no-raise variant that returns `(ok, reason)`.

- [ ] **Step 1: Write a failing unit test**

Create `apps/api/tests/unit/test_tier_worker_helper.py`:

```python
"""Unit test for try_enforce_entry_tier_limit."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.tier import try_enforce_entry_tier_limit


@pytest.mark.asyncio
async def test_under_limit_returns_true():
    mock_db = AsyncMock()
    mock_db.execute.return_value.scalar_one.return_value = 0  # 0 existing entries

    ok, reason = await try_enforce_entry_tier_limit(
        user_id=uuid.uuid4(),
        diary_id=uuid.uuid4(),
        source="auto",
        db=mock_db,
        subscription_tier="free",
    )
    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_at_limit_returns_false():
    mock_db = AsyncMock()
    mock_db.execute.return_value.scalar_one.return_value = 3  # at free-tier auto cap

    ok, reason = await try_enforce_entry_tier_limit(
        user_id=uuid.uuid4(),
        diary_id=uuid.uuid4(),
        source="auto",
        db=mock_db,
        subscription_tier="free",
    )
    assert ok is False
    assert reason is not None
    assert "limit" in reason.lower()


@pytest.mark.asyncio
async def test_unlimited_tier_returns_true():
    mock_db = AsyncMock()

    ok, reason = await try_enforce_entry_tier_limit(
        user_id=uuid.uuid4(),
        diary_id=uuid.uuid4(),
        source="auto",
        db=mock_db,
        subscription_tier="tier1",
    )
    assert ok is True
    assert reason is None
```

- [ ] **Step 2: Run to confirm test fails**

```bash
cd apps/api && pytest tests/unit/test_tier_worker_helper.py -v
```

Expected: FAIL — `try_enforce_entry_tier_limit` not yet defined.

- [ ] **Step 3: Add `try_enforce_entry_tier_limit` to `tier.py`**

Append to `apps/api/app/services/tier.py`:

```python
async def try_enforce_entry_tier_limit(
    user_id: uuid.UUID,
    diary_id: uuid.UUID,
    source: str,
    db: AsyncSession,
    subscription_tier: str = "free",
) -> tuple[bool, str | None]:
    """No-raise variant for use in Celery workers.

    Returns (True, None) if the user is within their limit, or (False, reason_str) if not.
    Unlike enforce_entry_tier_limit, never raises HTTPException.
    """
    try:
        await enforce_entry_tier_limit(
            user_id=user_id,
            diary_id=diary_id,
            source=source,
            db=db,
            subscription_tier=subscription_tier,
        )
        return True, None
    except HTTPException as exc:
        return False, str(exc.detail)
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
cd apps/api && pytest tests/unit/test_tier_worker_helper.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/tier.py apps/api/tests/unit/test_tier_worker_helper.py
git commit -m "feat: add try_enforce_entry_tier_limit no-raise helper for worker use"
```

---

### Task 6: Run full test suite

- [ ] **Step 1: Run all tests**

```bash
cd apps/api && make test
```

Expected: all unit and integration tests pass. If `test_scan_loop.py` or `test_entry_events.py` fail due to the `make_event` signature change, update those call sites: `make_event(db, entry, ...)` → `make_event(db, entry=entry, ...)`.

- [ ] **Step 2: Run lint + typecheck**

```bash
cd /Users/I549200/Desktop/working/code-projects/personal/perfect-day && make lint && make typecheck
```

Expected: zero errors.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -p
git commit -m "fix: update test call sites after make_event signature change"
```
