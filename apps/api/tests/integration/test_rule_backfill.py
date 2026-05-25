"""Integration tests for apply_rule_backfill."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AutoCreationRule, Entry
from app.workers.tasks import _apply_rule_backfill
from tests.fixtures.factories import make_event

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Wire the worker's db_session at the test database engine
# (same pattern as test_calendar_event_unattached.py and test_rules_evaluation.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def wire_worker_db(db_url):
    """Point the worker's db_session at the test database engine."""
    import app.core.database as db_module

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    original_engine = db_module._engine
    original_factory = db_module._session_factory

    db_module._engine = engine
    db_module._session_factory = factory

    yield

    db_module._engine = original_engine
    db_module._session_factory = original_factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup(client, email):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, auth, diary


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_backfill_creates_entries_for_matching_past_events(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Backfill creates entries for unattached events that match the rule."""
    token, auth, diary = await _setup(client, "backfill-creates@example.com")
    diary_id = uuid.UUID(diary["id"])

    rule = AutoCreationRule(
        diary_id=diary_id,
        name="soccer rule",
        condition={
            "op": "AND",
            "children": [
                {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}
            ],
        },
        options={"recurring": "per_instance", "multi_day": "per_day"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    # Soccer event (within 7 days)
    soccer_ev = await make_event(
        db_session,
        diary_id=diary_id,
        payload={
            "summary": "Soccer practice",
            "start": {"dateTime": "2026-06-01T10:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        },
        occurred_at=datetime.now(UTC) - timedelta(days=2),
    )
    # Piano event (does NOT match rule)
    piano_ev = await make_event(
        db_session,
        diary_id=diary_id,
        payload={
            "summary": "Piano lesson",
            "start": {"dateTime": "2026-06-02T10:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        },
        occurred_at=datetime.now(UTC) - timedelta(days=1),
    )
    await db_session.commit()

    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        await _apply_rule_backfill(str(rule.id), days=7)

    # Only soccer event should have an entry
    result = await db_session.execute(
        select(Entry).where(Entry.diary_id == diary_id).where(Entry.creation_source == "rule")
    )
    entries = result.scalars().all()
    assert len(entries) == 1
    assert mock_task.delay.call_count == 1

    # soccer_ev should be attached
    await db_session.refresh(soccer_ev)
    assert soccer_ev.entry_id is not None
    await db_session.refresh(piano_ev)
    assert piano_ev.entry_id is None


async def test_backfill_skips_already_attached_events(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Events already attached to entries are not re-processed by backfill."""
    token, auth, diary = await _setup(client, "backfill-skip-attached@example.com")
    diary_id = uuid.UUID(diary["id"])

    # Create an entry and attach an event manually
    entry_r = await client.post(
        f"/v1/diaries/{diary['id']}/entries",
        json={"entry_date": "2026-06-01"},
        headers=auth,
    )
    existing_entry_id = uuid.UUID(entry_r.json()["id"])

    rule = AutoCreationRule(
        diary_id=diary_id,
        name="soccer rule",
        condition={
            "op": "AND",
            "children": [
                {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}
            ],
        },
        options={"recurring": "per_instance", "multi_day": "per_day"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    ev = await make_event(
        db_session,
        diary_id=diary_id,
        payload={
            "summary": "Soccer practice",
            "start": {"dateTime": "2026-06-01T10:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        },
        occurred_at=datetime.now(UTC) - timedelta(days=2),
    )
    ev.entry_id = existing_entry_id
    await db_session.commit()

    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        await _apply_rule_backfill(str(rule.id), days=7)

    # No new entries should have been created
    result = await db_session.execute(
        select(Entry).where(Entry.diary_id == diary_id)
    )
    entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].id == existing_entry_id
    mock_task.delay.assert_not_called()


async def test_backfill_updates_last_applied_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Backfill sets rule.last_applied_at."""
    token, auth, diary = await _setup(client, "backfill-last-applied@example.com")
    diary_id = uuid.UUID(diary["id"])

    rule = AutoCreationRule(
        diary_id=diary_id,
        name="test rule",
        condition={
            "op": "AND",
            "children": [
                {"field": "title", "op": "contains", "value": "X", "case_sensitive": False}
            ],
        },
        options={"recurring": "per_instance", "multi_day": "per_day"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.commit()

    assert rule.last_applied_at is None

    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        await _apply_rule_backfill(str(rule.id), days=7)

    await db_session.refresh(rule)
    assert rule.last_applied_at is not None


async def test_backfill_nonexistent_rule_returns_silently(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Backfill with a nonexistent rule ID logs a warning and returns without error."""
    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        # Should not raise
        await _apply_rule_backfill(str(uuid.uuid4()), days=7)
    mock_task.delay.assert_not_called()
