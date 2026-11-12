"""The AI suggestion box (follow-up 25.3).

When an entry already has text, the AI never overwrites it: its version is
stored as a pending suggestion that the person can accept or dismiss.
Spec: docs/superpowers/specs/2026-11-11-code-review-decisions-design.md section 3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry


async def _setup(client: AsyncClient, email: str) -> tuple[dict, dict]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "Diary", "timezone": "UTC"}, headers=auth)
    ).json()
    return auth, diary


async def _create_entry(client: AsyncClient, diary_id: str, auth: dict, **fields) -> dict:
    r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": "2026-05-19", **fields},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _set_columns(db: AsyncSession, entry_id: str, **values) -> None:
    await db.execute(update(Entry).where(Entry.id == uuid.UUID(entry_id)).values(**values))
    await db.commit()


class TestSuggestionOnTheEntry:
    async def test_a_new_entry_has_no_suggestion(self, client):
        auth, diary = await _setup(client, "sg-none@example.com")
        entry = await _create_entry(client, diary["id"], auth)

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)

        assert r.json()["suggestion"] is None

    async def test_a_stored_suggestion_is_returned(self, client, db_session):
        auth, diary = await _setup(client, "sg-some@example.com")
        entry = await _create_entry(client, diary["id"], auth, body_markdown="Mine.")
        at = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
        await _set_columns(
            db_session,
            entry["id"],
            suggested_title="Park day",
            suggested_body_markdown="We went to the park.",
            suggested_flagged_tokens=["Park"],
            suggested_body_source="llm_hybrid",
            suggested_at=at,
        )

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)

        assert r.json()["body_markdown"] == "Mine."
        assert r.json()["suggestion"] == {
            "title": "Park day",
            "body_markdown": "We went to the park.",
            "flagged_tokens": ["Park"],
            "body_source": "llm_hybrid",
            "created_at": "2026-11-12T09:30:00Z",
        }

    async def test_missing_flagged_words_read_as_an_empty_list(self, client, db_session):
        auth, diary = await _setup(client, "sg-noflags@example.com")
        entry = await _create_entry(client, diary["id"], auth, body_markdown="Mine.")
        await _set_columns(
            db_session,
            entry["id"],
            suggested_body_markdown="Ours.",
            suggested_body_source="llm",
            suggested_at=datetime(2026, 11, 12, tzinfo=UTC),
        )

        r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)

        assert r.json()["suggestion"]["flagged_tokens"] == []
        assert r.json()["suggestion"]["title"] is None
