"""Integration tests: events and body_source fields on EntryOut API responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register(client: AsyncClient, email: str) -> str:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return r.json()["access_token"]


async def _create_diary(client: AsyncClient, auth: dict) -> dict:
    r = await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    assert r.status_code == 201
    return r.json()


async def _create_entry(
    client: AsyncClient,
    auth: dict,
    diary_id: str,
    entry_date: str = "2026-05-10",
) -> dict:
    r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": entry_date},
        headers=auth,
    )
    assert r.status_code == 201
    return r.json()


async def _seed_events(
    db: AsyncSession, entry_id: str, events_data: list[dict]
) -> list[Event]:
    """Directly insert Event rows for an entry, bypassing the API."""
    events = []
    for data in events_data:
        ev = Event(
            entry_id=uuid.UUID(entry_id),
            source=data.get("source", "google_calendar"),
            external_id=data.get("external_id"),
            occurred_at=data.get("occurred_at"),
            payload=data.get("payload", {}),
        )
        db.add(ev)
        events.append(ev)
    await db.flush()
    await db.commit()
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntryEventsField:
    async def test_list_entries_has_events_and_body_source(self, client: AsyncClient):
        """GET /v1/diaries/{id}/entries returns entries with events array and body_source."""
        token = await _register(client, "eetest1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        _ = await _create_entry(client, auth, diary["id"])

        r = await client.get(f"/v1/diaries/{diary['id']}/entries", headers=auth)
        assert r.status_code == 200
        entries = r.json()
        assert len(entries) == 1
        entry = entries[0]
        assert "events" in entry
        assert isinstance(entry["events"], list)
        assert "body_source" in entry
        assert entry["body_source"] == "llm"  # default

    async def test_get_entry_has_events_and_body_source(self, client: AsyncClient):
        """GET /v1/entries/{id} returns entry with events array and body_source."""
        token = await _register(client, "eetest2@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "body_source" in data
        assert data["body_source"] == "llm"

    async def test_entry_with_no_events_returns_empty_list(self, client: AsyncClient):
        """Entry with no attached events returns events: []."""
        token = await _register(client, "eetest3@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["events"] == []

    async def test_get_entry_returns_event_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /v1/entries/{id} returns full EventOut with summary, source, occurred_at etc."""
        token = await _register(client, "eetest4@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        occurred = datetime(2026, 5, 10, 16, 0, tzinfo=UTC)
        await _seed_events(
            db_session,
            entry["id"],
            [
                {
                    "source": "google_calendar",
                    "external_id": "evt-eetest4-001",
                    "occurred_at": occurred,
                    "payload": {
                        "summary": "Soccer practice",
                        "description": "Weekly practice",
                        "location": "Main Field",
                        "start": {"dateTime": "2026-05-10T16:00:00Z"},
                        "end": {"dateTime": "2026-05-10T17:00:00Z"},
                        "attendees": [{"email": "coach@example.com"}],
                        "status": "confirmed",
                    },
                }
            ],
        )

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
        assert r.status_code == 200
        data = r.json()
        assert len(data["events"]) == 1
        ev = data["events"][0]
        assert ev["source"] == "google_calendar"
        assert ev["summary"] == "Soccer practice"
        assert ev["description"] == "Weekly practice"
        assert ev["location"] == "Main Field"
        assert ev["status"] == "confirmed"
        assert ev["occurred_at"] is not None
        assert ev["start"] == {"dateTime": "2026-05-10T16:00:00Z"}
        assert ev["end"] == {"dateTime": "2026-05-10T17:00:00Z"}
        assert len(ev["attendees"]) == 1
        assert ev["attendees"][0]["email"] == "coach@example.com"

    async def test_events_sorted_by_occurred_at(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Events are returned sorted by occurred_at ascending."""
        token = await _register(client, "eetest5@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        # Insert later event first to confirm sorting
        await _seed_events(
            db_session,
            entry["id"],
            [
                {
                    "source": "google_calendar",
                    "external_id": "evt-late-001",
                    "occurred_at": datetime(2026, 5, 10, 19, 0, tzinfo=UTC),
                    "payload": {"summary": "Dinner"},
                },
                {
                    "source": "google_calendar",
                    "external_id": "evt-early-001",
                    "occurred_at": datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
                    "payload": {"summary": "Breakfast"},
                },
            ],
        )

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
        assert r.status_code == 200
        events = r.json()["events"]
        assert len(events) == 2
        assert events[0]["summary"] == "Breakfast"
        assert events[1]["summary"] == "Dinner"

    async def test_list_entries_includes_events(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /v1/diaries/{id}/entries returns populated events (not empty when events exist)."""
        token = await _register(client, "eetest6@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        await _seed_events(
            db_session,
            entry["id"],
            [
                {
                    "source": "google_calendar",
                    "external_id": "evt-list-001",
                    "occurred_at": datetime(2026, 5, 10, 14, 0, tzinfo=UTC),
                    "payload": {"summary": "Afternoon walk"},
                },
            ],
        )

        r = await client.get(f"/v1/diaries/{diary['id']}/entries", headers=auth)
        assert r.status_code == 200
        entries = r.json()
        assert len(entries) == 1
        assert len(entries[0]["events"]) == 1
        assert entries[0]["events"][0]["summary"] == "Afternoon walk"
