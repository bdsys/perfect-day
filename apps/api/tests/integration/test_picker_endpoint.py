"""Integration tests for calendar-events list and entries/from-event endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Event
from tests.fixtures.factories import make_event


async def _setup(client: AsyncClient, email: str):
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

        await make_event(
            db_session,
            diary_id=diary_id,
            payload={
                "summary": "Soccer",
                "location": "Park",
                "description": "",
                "start": {"dateTime": "2026-05-20T10:00:00Z"},
                "end": {},
                "status": "",
                "attendees": [],
            },
            occurred_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )
        await make_event(
            db_session,
            diary_id=diary_id,
            payload={
                "summary": "Piano",
                "location": "",
                "description": "",
                "start": {"dateTime": "2026-05-21T14:00:00Z"},
                "end": {},
                "status": "",
                "attendees": [],
            },
            occurred_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        )

        r = await client.get(
            f"/v1/diaries/{diary['id']}/calendar-events", headers=auth
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
            payload={
                "summary": "Attached event",
                "location": "",
                "description": "",
                "start": {},
                "end": {},
                "status": "",
                "attendees": [],
            },
        )
        await db_session.execute(
            update(Event).where(Event.id == ev.id).values(entry_id=uuid.UUID(entry["id"]))
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

        with patch("app.routers.v1.calendar_events.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
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

        mock_task.delay.assert_called_once_with(data["id"])

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
        await db_session.execute(
            update(Event).where(Event.id == ev.id).values(entry_id=uuid.UUID(entry["id"]))
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

        # Create a second real user + diary so the FK constraint is satisfied
        r2 = await client.post(
            "/v1/auth/register",
            json={"email": "picker-other-owner@example.com", "password": "Password1!"},
        )
        auth2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        other_diary = (
            await client.post(
                "/v1/diaries", json={"name": "Other", "timezone": "UTC"}, headers=auth2
            )
        ).json()
        other_diary_id = uuid.UUID(other_diary["id"])
        ev = await make_event(db_session, diary_id=other_diary_id)

        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries/from-event",
            json={"event_id": str(ev.id)},
            headers=auth,
        )
        assert r.status_code == 404

    async def test_task_queue_failure_still_returns_201(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, auth, diary = await _setup(client, "picker-queue-fail@example.com")
        diary_id = uuid.UUID(diary["id"])

        ev = await make_event(db_session, diary_id=diary_id)

        with patch("app.routers.v1.calendar_events.generate_entry_draft") as mock_task:
            mock_task.delay.side_effect = Exception("broker unavailable")
            r = await client.post(
                f"/v1/diaries/{diary['id']}/entries/from-event",
                json={"event_id": str(ev.id)},
                headers=auth,
            )

        assert r.status_code == 201
        data = r.json()
        assert data["creation_source"] == "calendar_pick"
        await db_session.refresh(ev)
        assert ev.entry_id == uuid.UUID(data["id"])
