"""Integration test: rule evaluation respects free-tier entry limit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_event

pytestmark = pytest.mark.asyncio


async def _setup(client, email):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, auth, diary


async def test_free_tier_limit_stops_rule_entries(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Free tier allows 3 auto entries. 4th event should NOT create an entry."""
    token, auth, diary = await _setup(client, "tier-limit-rules@example.com")
    diary_id = uuid.UUID(diary["id"])

    # Create rule that matches everything with "Meeting" in the title
    rule = AutoCreationRule(
        diary_id=diary_id,
        name="all meetings",
        condition={
            "op": "AND",
            "children": [
                {"field": "title", "op": "contains", "value": "Meeting", "case_sensitive": False}
            ],
        },
        options={"recurring": "per_instance", "multi_day": "per_day"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()
    await db_session.commit()

    # Create 4 events with "Meeting" in the title
    events = []
    for i in range(4):
        ev = await make_event(
            db_session,
            diary_id=diary_id,
            payload={
                "summary": f"Meeting {i + 1}",
                "start": {"dateTime": f"2026-06-{i + 1:02d}T10:00:00Z"},
                "end": {},
                "attendees": [],
                "description": "",
                "location": "",
                "status": "",
            },
            occurred_at=datetime(2026, 6, i + 1, 10, 0, tzinfo=UTC),
        )
        events.append(ev)
    await db_session.commit()

    # Process all 4 events
    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        for ev in events:
            await evaluate_event_against_rules(str(ev.id), str(diary_id), db_session)

    # Only 3 entries should have been created (free tier limit = 3 auto)
    result = await db_session.execute(
        select(Entry).where(Entry.diary_id == diary_id).where(Entry.creation_source == "rule")
    )
    entries = result.scalars().all()
    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"
    assert mock_task.delay.call_count == 3
