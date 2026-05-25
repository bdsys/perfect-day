# Calendar Entry Refactor — Part 2: Calendar Picker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Part 1 (schema + worker refactor) must be complete. `Event.entry_id` is nullable; events are stored with `entry_id=NULL` after scan.

**Goal:** Let users create a diary entry from a specific synced calendar event. "New entry from Google Calendar" opens a date-grouped picker; clicking an event creates a draft, attaches the event, queues LLM generation, and navigates to the entry detail page (which already polls for body).

**Architecture:** Two new backend endpoints (`GET /calendar-events`, `POST /entries/from-event`) in a new `calendar_events` router. A new Next.js page at `/diaries/[diaryId]/calendar-pick` renders the picker. Two new buttons added to the diary detail page header.

**Tech Stack:** Python, FastAPI, SQLAlchemy async, pytest; Next.js 14 App Router, TypeScript

---

## File Map

| Action | Path |
|---|---|
| **Create** | `apps/api/app/routers/v1/calendar_events.py` |
| **Modify** | `apps/api/app/main.py` |
| **Modify** | `apps/api/app/routers/v1/entries.py` (add `creation_source` to `EntryOut`) |
| **Create** | `apps/api/tests/integration/test_picker_endpoint.py` |
| **Modify** | `apps/web/src/lib/api.ts` |
| **Create** | `apps/web/src/app/diaries/[diaryId]/calendar-pick/page.tsx` |
| **Modify** | `apps/web/src/app/diaries/[diaryId]/page.tsx` |

---

### Task 1: Backend — `GET /v1/diaries/{diary_id}/calendar-events`

**Files:**
- Create: `apps/api/app/routers/v1/calendar_events.py`

- [ ] **Step 1: Write the failing integration test first**

Create `apps/api/tests/integration/test_picker_endpoint.py`:

```python
"""Integration tests for calendar-events list and entries/from-event endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Event
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user


async def _auth(client: AsyncClient, email: str) -> tuple[str, dict]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


class TestListCalendarEvents:
    async def test_lists_unattached_events(self, client: AsyncClient, db_session: AsyncSession):
        token, auth = await _auth(client, "picker1@example.com")
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()

        # seed two unattached events via factory
        u = (await db_session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(
                __import__("app.models", fromlist=["User"]).User
            ).where(__import__("app.models", fromlist=["User"]).User.email == "picker1@example.com")
        )).scalar_one()
        from tests.fixtures.factories import make_diary as _md
        from app.models import Diary as _D
        d = (await db_session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(_D).where(_D.id == uuid.UUID(diary["id"]))
        )).scalar_one()
        ev1 = await make_event(db_session, payload={"summary": "Soccer", "location": "Park", "description": "", "start": {}, "end": {}, "status": "", "attendees": []}, occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC))
        ev2 = await make_event(db_session, payload={"summary": "Piano", "location": "", "description": "", "start": {}, "end": {}, "status": "", "attendees": []}, occurred_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC))

        # Manually set the diary_id context — events have no diary_id column but the endpoint
        # filters by diary's events via a join. Inject entry for the diary to verify the filter.
        # Actually: since events are unattached, the endpoint needs another way to scope them.
        # The endpoint will scope by NOT having an entry — events scoped to a diary require
        # the diary_id to be stored on the event. See architecture note below.

        r = await client.get(f"/v1/diaries/{diary['id']}/calendar-events", headers=auth)
        assert r.status_code == 200

    async def test_requires_auth(self, client: AsyncClient):
        r = await client.get(f"/v1/diaries/{uuid.uuid4()}/calendar-events")
        assert r.status_code == 401
```

**Architecture note:** The `Event` table has no `diary_id` column. An unattached event (`entry_id IS NULL`) cannot be directly scoped to a diary. We must add `diary_id` to the `Event` table. This was missed in Part 1.

**Stop. Add `diary_id` to `Event` before writing the endpoint.**

- [ ] **Step 2: Add `diary_id` to the Event model and migration**

The migration `0005` must be amended to add a nullable `diary_id` FK on `events`:

In `apps/api/alembic/versions/0005_decouple_events_and_rules.py`, in `upgrade()`, add after the `alter_column("events", "entry_id", nullable=True)` call:

```python
    op.add_column(
        "events",
        sa.Column(
            "diary_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # Back-fill diary_id for existing attached events from their entry's diary_id
    op.execute(
        """
        UPDATE events e
        SET diary_id = en.diary_id
        FROM entries en
        WHERE e.entry_id = en.id
          AND e.diary_id IS NULL
        """
    )
    op.create_index("ix_events_diary_id", "events", ["diary_id"])
```

In `downgrade()`, before the `alter_column` call add:

```python
    op.drop_index("ix_events_diary_id", table_name="events")
    op.drop_column("events", "diary_id")
```

In `apps/api/app/models/__init__.py`, update the `Event` class to add `diary_id`:

```python
class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=True
    )
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    entry: Mapped[Entry | None] = relationship(back_populates="events", foreign_keys=[entry_id])

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
        Index("ix_events_diary_id", "diary_id"),
        CheckConstraint(
            "source IN ('google_calendar','google_photos','manual','spotify')",
            name="ck_events_source",
        ),
    )
```

Also update `_ingest_calendar_event` in `tasks.py` to set `diary_id` on the Event:

```python
        event = Event(
            diary_id=diary_id,
            entry_id=None,
            source=source,
            external_id=external_id,
            occurred_at=occurred_at,
            payload=payload,
        )
```

Also update `make_event` in `factories.py` to accept `diary_id`:

```python
async def make_event(
    db: AsyncSession,
    diary_id: uuid.UUID | None = None,
    entry: Entry | None = None,
    source: str = "google_calendar",
    external_id: str | None = None,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    ev = Event(
        diary_id=diary_id if diary_id is not None else (entry.diary_id if entry is not None else None),
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

Run the migration again:

```bash
cd apps/api && alembic downgrade base && alembic upgrade head
```

(Or `alembic downgrade -1 && alembic upgrade head` if you don't want to re-run from scratch.)

- [ ] **Step 3: Rewrite the test now that `diary_id` exists on Event**

Replace `apps/api/tests/integration/test_picker_endpoint.py`:

```python
"""Integration tests for calendar-events list and entries/from-event endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Event
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user


async def _setup(client, email: str):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, auth, diary


class TestListCalendarEvents:
    async def test_lists_unattached_events(self, client: AsyncClient, db_session: AsyncSession):
        token, auth, diary = await _setup(client, "picker-list@example.com")
        diary_id = uuid.UUID(diary["id"])

        ev1 = await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "Soccer", "location": "Park", "description": "", "start": {"dateTime": "2026-05-20T10:00:00Z"}, "end": {}, "status": "", "attendees": []},
            occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )
        ev2 = await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "Piano", "location": "", "description": "", "start": {"dateTime": "2026-05-21T14:00:00Z"}, "end": {}, "status": "", "attendees": []},
            occurred_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        )

        r = await client.get(
            f"/v1/diaries/{diary['id']}/calendar-events?attached=false", headers=auth
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        summaries = {e["summary"] for e in data}
        assert summaries == {"Soccer", "Piano"}

    async def test_does_not_return_attached_events_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, auth, diary = await _setup(client, "picker-attached@example.com")
        diary_id = uuid.UUID(diary["id"])

        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2026-05-20"},
                headers=auth,
            )
        ).json()
        ev = await make_event(
            db_session,
            diary_id=diary_id,
            entry=None,
            payload={"summary": "Attached event", "location": "", "description": "", "start": {}, "end": {}, "status": "", "attendees": []},
        )
        # Manually attach
        from sqlalchemy import update
        from app.models import Event as _Ev
        await db_session.execute(
            update(_Ev).where(_Ev.id == ev.id).values(entry_id=uuid.UUID(entry["id"]))
        )
        await db_session.commit()

        r = await client.get(
            f"/v1/diaries/{diary['id']}/calendar-events", headers=auth
        )
        assert r.status_code == 200
        assert all(e["summary"] != "Attached event" for e in r.json())

    async def test_requires_auth(self, client: AsyncClient):
        r = await client.get(f"/v1/diaries/{uuid.uuid4()}/calendar-events")
        assert r.status_code == 401


class TestCreateFromEvent:
    async def test_creates_entry_attaches_event_queues_llm(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, auth, diary = await _setup(client, "picker-create@example.com")
        diary_id = uuid.UUID(diary["id"])

        ev = await make_event(
            db_session,
            diary_id=diary_id,
            payload={
                "summary": "Soccer practice",
                "location": "City Park",
                "description": "",
                "start": {"dateTime": "2026-05-20T10:00:00Z"},
                "end": {"dateTime": "2026-05-20T11:30:00Z"},
                "status": "confirmed",
                "attendees": [],
            },
            occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = lambda entry_id: None
            r = await client.post(
                f"/v1/diaries/{diary['id']}/entries/from-event",
                json={"event_id": str(ev.id)},
                headers=auth,
            )

        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "draft"
        assert data["created_by"] == "manual"
        assert data["creation_source"] == "calendar_pick"
        assert len(data["events"]) == 1
        assert data["events"][0]["summary"] == "Soccer practice"

        # Verify event is now attached in DB
        await db_session.refresh(ev)
        assert ev.entry_id == uuid.UUID(data["id"])

    async def test_returns_409_if_event_already_attached(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, auth, diary = await _setup(client, "picker-409@example.com")
        diary_id = uuid.UUID(diary["id"])

        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2026-05-20"},
                headers=auth,
            )
        ).json()

        ev = await make_event(db_session, diary_id=diary_id)
        from sqlalchemy import update
        from app.models import Event as _Ev
        await db_session.execute(
            update(_Ev).where(_Ev.id == ev.id).values(entry_id=uuid.UUID(entry["id"]))
        )
        await db_session.commit()

        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries/from-event",
            json={"event_id": str(ev.id)},
            headers=auth,
        )
        assert r.status_code == 409

    async def test_returns_404_if_event_belongs_to_different_diary(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, auth, diary = await _setup(client, "picker-wrong-diary@example.com")

        # Event with a different diary_id
        other_diary_id = uuid.uuid4()
        ev = await make_event(db_session, diary_id=other_diary_id)

        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries/from-event",
            json={"event_id": str(ev.id)},
            headers=auth,
        )
        assert r.status_code == 404
```

- [ ] **Step 4: Run the tests to confirm they fail (endpoint not yet built)**

```bash
cd apps/api && pytest tests/integration/test_picker_endpoint.py -v
```

Expected: FAIL with 404 or 405 (endpoint not registered yet).

- [ ] **Step 5: Write the router**

Create `apps/api/app/routers/v1/calendar_events.py`:

```python
"""Endpoints for listing unattached calendar events and creating entries from them."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Diary, Entry, Event, User
from app.routers.v1.diaries import _get_diary_or_404
from app.routers.v1.entries import EntryOut, _entry_out_from_orm, _event_out_from_orm
from app.services.tier import enforce_entry_tier_limit
from app.workers.tz_utils import google_event_to_entry_date

router = APIRouter(tags=["calendar_events"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CalendarEventSummary(BaseModel):
    id: uuid.UUID
    summary: str
    description: str
    location: str
    occurred_at: datetime | None
    start: dict
    end: dict
    attendees: list[dict]
    status: str

    model_config = {"from_attributes": False}


class CreateFromEventBody(BaseModel):
    event_id: uuid.UUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calendar_event_summary(event: Event) -> CalendarEventSummary:
    p = event.payload or {}
    return CalendarEventSummary(
        id=event.id,
        summary=p.get("summary", ""),
        description=p.get("description", ""),
        location=p.get("location", ""),
        occurred_at=event.occurred_at,
        start=p.get("start", {}),
        end=p.get("end", {}),
        attendees=p.get("attendees", []),
        status=p.get("status", ""),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/diaries/{diary_id}/calendar-events",
    response_model=list[CalendarEventSummary],
)
async def list_calendar_events(
    diary_id: uuid.UUID,
    attached: bool = Query(False),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    cursor: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarEventSummary]:
    """List calendar events synced to this diary.

    By default returns only unattached events (entry_id IS NULL).
    Pass attached=true to include all events regardless of attachment.
    """
    await _get_diary_or_404(diary_id, user, db)

    q = select(Event).where(Event.diary_id == diary_id)

    if not attached:
        q = q.where(Event.entry_id.is_(None))

    if from_date:
        q = q.where(Event.occurred_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.where(Event.occurred_at <= datetime.combine(to_date, datetime.max.time()))

    q = q.order_by(Event.occurred_at.desc()).limit(limit)

    result = await db.execute(q)
    return [_calendar_event_summary(e) for e in result.scalars()]


@router.post(
    "/diaries/{diary_id}/entries/from-event",
    response_model=EntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry_from_event(
    diary_id: uuid.UUID,
    body: CreateFromEventBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    """Create a new entry from a specific unattached calendar event.

    The event is attached to the new entry and LLM generation is queued.
    The client should poll the returned entry's updated_at to detect when
    body_markdown has been populated.
    """
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    # Load the event and validate ownership / attachment status
    event_result = await db.execute(select(Event).where(Event.id == body.event_id))
    event = event_result.scalar_one_or_none()

    if event is None or event.diary_id != diary_id:
        raise HTTPException(status_code=404, detail="event_not_found")

    if event.entry_id is not None:
        raise HTTPException(status_code=409, detail="event_already_attached")

    await enforce_entry_tier_limit(
        user_id=user.id,
        diary_id=diary_id,
        source="manual",
        db=db,
        subscription_tier=user.subscription_tier,
    )

    # Derive entry_date from the event's payload (same logic as ingest worker)
    p = event.payload or {}
    raw_event = {
        "start": p.get("start", {}),
        "end": p.get("end", {}),
    }
    entry_date, entry_end_date = google_event_to_entry_date(raw_event, diary.timezone)
    if entry_date is None:
        from datetime import date as _date
        entry_date = event.occurred_at.date() if event.occurred_at else _date.today()

    entry = Entry(
        diary_id=diary_id,
        entry_date=entry_date,
        entry_end_date=entry_end_date,
        status="draft",
        created_by="manual",
        creation_source="calendar_pick",
    )
    db.add(entry)
    await db.flush()

    event.entry_id = entry.id
    await db.flush()
    await db.refresh(entry, ["events"])

    from app.workers.tasks import generate_entry_draft
    generate_entry_draft.delay(str(entry.id))

    return _entry_out_from_orm(entry)
```

- [ ] **Step 6: Register the router in `main.py`**

In `apps/api/app/main.py`, add `calendar_events` to the import and register it:

```python
    from app.routers.v1 import auth, calendar_events, diaries, entries, integrations, scan

    app.include_router(auth.router, prefix="/v1")
    app.include_router(diaries.router, prefix="/v1")
    app.include_router(entries.router, prefix="/v1")
    app.include_router(calendar_events.router, prefix="/v1")
    app.include_router(integrations.router, prefix="/v1")
    app.include_router(scan.router, prefix="/v1")
```

- [ ] **Step 7: Add `creation_source` to `EntryOut`**

In `apps/api/app/routers/v1/entries.py`, update `EntryOut`:

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

    model_config = {"from_attributes": True}
```

- [ ] **Step 8: Run the tests to confirm they pass**

```bash
cd apps/api && pytest tests/integration/test_picker_endpoint.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 9: Run the full suite**

```bash
cd apps/api && make test
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add \
  apps/api/alembic/versions/0005_decouple_events_and_rules.py \
  apps/api/app/models/__init__.py \
  apps/api/app/routers/v1/calendar_events.py \
  apps/api/app/routers/v1/entries.py \
  apps/api/app/main.py \
  apps/api/app/workers/tasks.py \
  apps/api/tests/fixtures/factories.py \
  apps/api/tests/integration/test_picker_endpoint.py
git commit -m "feat: add calendar-events list and create-entry-from-event endpoints"
```

---

### Task 2: Frontend — API client types and functions

**Files:**
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: Add `CalendarEventSummary` type and `creation_source` to `Entry`**

In `apps/web/src/lib/api.ts`, add after the `EventItem` interface:

```typescript
export interface CalendarEventSummary {
  id: string
  summary: string
  description: string
  location: string
  occurred_at: string | null
  start: Record<string, string>
  end: Record<string, string>
  attendees: Array<{ displayName: string; email: string; organizer: boolean; responseStatus: string }>
  status: string
}
```

Add `creation_source` to the `Entry` interface:

```typescript
export interface Entry {
  id: string
  diary_id: string
  entry_date: string
  entry_end_date: string | null
  title: string | null
  body_markdown: string | null
  body_source: 'llm' | 'fallback'
  flagged_tokens: string[] | null
  status: 'draft' | 'published'
  created_by: 'auto' | 'manual'
  creation_source: 'manual' | 'calendar_pick' | 'rule' | 'legacy_auto'
  published_at: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
  events: EventItem[]
}
```

- [ ] **Step 2: Add `calendarEvents` namespace to the `api` object**

In `api.ts`, add after the `integrations` block (before the closing `}`):

```typescript
  calendarEvents: {
    async list(
      diaryId: string,
      params: { attached?: boolean; from?: string; to?: string; limit?: number } = {},
    ): Promise<CalendarEventSummary[]> {
      const q = new URLSearchParams()
      if (params.attached !== undefined) q.set('attached', String(params.attached))
      if (params.from) q.set('from', params.from)
      if (params.to) q.set('to', params.to)
      if (params.limit) q.set('limit', String(params.limit))
      const qs = q.toString()
      return apiFetch(`/v1/diaries/${diaryId}/calendar-events${qs ? '?' + qs : ''}`)
    },

    async createFromEvent(diaryId: string, eventId: string): Promise<Entry> {
      return apiFetch(`/v1/diaries/${diaryId}/entries/from-event`, {
        method: 'POST',
        body: JSON.stringify({ event_id: eventId }),
      })
    },
  },
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/api.ts
git commit -m "feat: add CalendarEventSummary type and calendarEvents API client"
```

---

### Task 3: Frontend — Diary page buttons

**Files:**
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`

- [ ] **Step 1: Add the two new buttons to the page actions**

In `apps/web/src/app/diaries/[diaryId]/page.tsx`, in the `page-actions` div, add the two new buttons after the "New entry" button:

```tsx
            <button className="btn btn-primary" onClick={handleNewEntry} disabled={creating}>
              {creating ? 'Creating…' : 'New entry'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => router.push(`/diaries/${diaryId}/calendar-pick`)}
            >
              New entry from Google Calendar
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => router.push(`/diaries/${diaryId}/rules`)}
            >
              Auto-Creation Rules
            </button>
```

- [ ] **Step 2: Update the empty state copy**

Replace the empty state paragraph:

```tsx
          <div className="empty-state">
            <p>
              No entries yet. Create one manually, pick from Google Calendar, or{' '}
              <Link href={`/diaries/${diaryId}/rules`}>set up auto-creation rules</Link>.
            </p>
          </div>
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/app/diaries/[diaryId]/page.tsx
git commit -m "feat: add 'New entry from Google Calendar' and 'Auto-Creation Rules' buttons"
```

---

### Task 4: Frontend — Calendar picker page

**Files:**
- Create: `apps/web/src/app/diaries/[diaryId]/calendar-pick/page.tsx`

The page:
1. Loads unattached events via `api.calendarEvents.list(diaryId)`.
2. Groups them by date.
3. Clicking an event calls `api.calendarEvents.createFromEvent(diaryId, event.id)`.
4. On success: navigates to `/entries/[id]`. The entry detail page already polls `updated_at` via its existing Regenerate polling — we'll start that polling automatically so the user sees the LLM generation progress.
5. On 409: refreshes the list and shows an error.

- [ ] **Step 1: Write the picker page**

```tsx
'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type CalendarEventSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function formatOccurredAt(event: CalendarEventSummary): string {
  const dtStr = event.start?.dateTime ?? event.start?.date ?? event.occurred_at
  if (!dtStr) return 'Unknown time'
  if (event.start?.date && !event.start?.dateTime) return 'All day'
  const dt = new Date(dtStr)
  const time = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const endDtStr = event.end?.dateTime
  if (!endDtStr) return time
  const endTime = new Date(endDtStr).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return `${time}–${endTime}`
}

function groupByDate(events: CalendarEventSummary[]): Map<string, CalendarEventSummary[]> {
  const map = new Map<string, CalendarEventSummary[]>()
  for (const ev of events) {
    const dateKey = ev.start?.date
      ?? (ev.start?.dateTime ? ev.start.dateTime.slice(0, 10) : null)
      ?? (ev.occurred_at ? ev.occurred_at.slice(0, 10) : 'unknown')
    const bucket = map.get(dateKey) ?? []
    bucket.push(ev)
    map.set(dateKey, bucket)
  }
  // Sort dates descending
  return new Map([...map.entries()].sort((a, b) => b[0].localeCompare(a[0])))
}

function formatDateHeading(dateStr: string): string {
  if (dateStr === 'unknown') return 'Unknown date'
  return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default function CalendarPickPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [events, setEvents] = useState<CalendarEventSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState<string | null>(null) // event id being created

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.calendarEvents.list(diaryId, { attached: false })
      .then(setEvents)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load events'))
      .finally(() => setLoading(false))
  }, [user, diaryId])

  async function handlePick(event: CalendarEventSummary) {
    setCreating(event.id)
    setError('')
    try {
      const entry = await api.calendarEvents.createFromEvent(diaryId, event.id)
      router.push(`/entries/${entry.id}?fromPick=1`)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to create entry'
      if (msg.includes('409') || msg.includes('event_already_attached')) {
        setError('That event was just claimed. Refreshing the list…')
        const refreshed = await api.calendarEvents.list(diaryId, { attached: false })
        setEvents(refreshed)
      } else {
        setError(msg)
      }
      setCreating(null)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  const grouped = groupByDate(events)

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <h1 className="page-title">New entry from Google Calendar</h1>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Click an event to create a diary entry from it. The LLM will generate a draft using the event details.
        </p>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {events.length === 0 ? (
          <div className="empty-state">
            <p>No unattached calendar events found. Try running a scan first.</p>
          </div>
        ) : (
          [...grouped.entries()].map(([dateKey, dayEvents]) => (
            <div key={dateKey} style={{ marginBottom: '1.5rem' }}>
              <div style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                marginBottom: '0.5rem',
              }}>
                {formatDateHeading(dateKey)}
              </div>
              {dayEvents.map((ev) => (
                <button
                  key={ev.id}
                  className="entry-card"
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    cursor: creating !== null ? 'not-allowed' : 'pointer',
                    opacity: creating === ev.id ? 0.5 : 1,
                    border: 'none',
                    background: 'var(--card-bg)',
                    marginBottom: '0.5rem',
                  }}
                  disabled={creating !== null}
                  onClick={() => handlePick(ev)}
                >
                  <div className="entry-title">
                    {creating === ev.id ? 'Creating…' : (ev.summary || '(no title)')}
                  </div>
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    {formatOccurredAt(ev)}
                    {ev.location ? ` · ${ev.location}` : ''}
                    {ev.attendees?.length > 0 ? ` · ${ev.attendees.length} attendee${ev.attendees.length !== 1 ? 's' : ''}` : ''}
                  </div>
                  {ev.description && (
                    <div style={{ fontSize: '0.8rem', color: '#999', marginTop: '0.2rem' }}>
                      {ev.description.slice(0, 80)}{ev.description.length > 80 ? '…' : ''}
                    </div>
                  )}
                </button>
              ))}
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
git add apps/web/src/app/diaries/[diaryId]/calendar-pick/page.tsx
git commit -m "feat: add calendar picker page for creating entries from synced events"
```

---

### Task 5: Frontend — Auto-start LLM polling on entry detail when arriving from picker

The entry detail page currently polls only when Regenerate is clicked (`pollingRegen = true`). When arriving from the picker (`?fromPick=1`), the entry has no `body_markdown` yet, so we should auto-start polling immediately.

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

- [ ] **Step 1: Read the `fromPick` query param and auto-start polling**

In `apps/web/src/app/entries/[entryId]/page.tsx`:

1. Add `useSearchParams` to the Next.js imports at the top:
```tsx
import { useParams, useRouter, useSearchParams } from 'next/navigation'
```

2. Inside `EntryDetailPage`, after `const router = useRouter()`, add:
```tsx
  const searchParams = useSearchParams()
  const fromPick = searchParams.get('fromPick') === '1'
```

3. In the `useEffect` that loads the entry (after `setEntry(e)`), auto-start regen polling if we arrived from the picker and there's no body yet:
```tsx
  useEffect(() => {
    if (!user || !entryId) return
    api.entries.get(entryId)
      .then((e) => {
        setEntry(e)
        setEditTitle(e.title ?? '')
        setEditBody(e.body_markdown ?? '')
        // If we arrived from the picker, start polling for LLM body immediately
        if (fromPick && !e.body_markdown) {
          setRegenStartedAt(e.updated_at)
          setRegenStartTime(new Date().toISOString())
          setPollingRegen(true)
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, entryId, fromPick])
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/app/entries/[entryId]/page.tsx
git commit -m "feat: auto-start LLM body polling when arriving at entry from calendar picker"
```

---

### Task 6: Verify end-to-end

- [ ] **Step 1: Run the full backend test suite**

```bash
cd apps/api && make test
```

Expected: all tests pass.

- [ ] **Step 2: Run lint and typecheck across the full repo**

```bash
cd /Users/I549200/Desktop/working/code-projects/personal/perfect-day && make lint && make typecheck
```

Expected: zero errors.

- [ ] **Step 3: Manual smoke test (requires running stack)**

```bash
cd /Users/I549200/Desktop/working/code-projects/personal/perfect-day && make up
```

Verify:
1. Navigate to a diary → two new buttons appear: "New entry from Google Calendar" and "Auto-Creation Rules".
2. Click "New entry from Google Calendar" → `/diaries/[id]/calendar-pick` loads with "No unattached calendar events" if no scan has been run.
3. Trigger a scan → navigate back to picker → events appear grouped by date.
4. Click an event → navigated to `/entries/[id]` with a spinner showing "Regenerating draft…" → body fills in within ~10s.
5. Refresh entry — body persists, events accordion shows the source event.
