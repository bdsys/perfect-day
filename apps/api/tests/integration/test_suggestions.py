"""The AI suggestion box (follow-up 25.3).

When an entry already has text, the AI never overwrites it: its version is
stored as a pending suggestion that the person can accept or dismiss.
Spec: docs/superpowers/specs/2026-11-11-code-review-decisions-design.md section 3.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.workers.llm as worker_llm
from app.models import Entry, LLMGeneration
from app.workers.llm_providers import LLMPermanentError, LLMResult
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user

_PICNIC = {
    "summary": "Picnic in the park",
    "location": "",
    "description": "",
    "start": {"dateTime": "2026-05-19T12:00:00+00:00"},
    "end": {"dateTime": "2026-05-19T13:00:00+00:00"},
    "status": "confirmed",
    "attendees": [],
}


def _reply(body: str, title: str = "Picnic day", facts: list[int] | None = None) -> str:
    return json.dumps({
        "title": title,
        "title_facts_used": [],
        "body_markdown": body,
        "facts_used": [1] if facts is None else facts,
    })


def _provider(raw: str | None = None, *, fail: bool = False, before_reply=None) -> MagicMock:
    """A configured provider whose reply is *raw* (or a permanent failure)."""

    async def generate(system_prompt, diary_context, entry_data):
        if before_reply is not None:
            await before_reply()
        if fail:
            raise LLMPermanentError("mocked failure")
        return LLMResult(raw_text=raw, input_tokens=10, output_tokens=5, model="claude-opus-5-5")

    p = MagicMock()
    p.name = "anthropic"
    p.is_configured.return_value = True
    p.generate = AsyncMock(side_effect=generate)
    return p


async def _run_draft(db_url: str, entry_id: uuid.UUID, provider: MagicMock, **kwargs) -> None:
    """Run the drafting worker against the test database with *provider* as the only model."""
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s
            await s.commit()

    gemini = MagicMock()
    gemini.name = "gemini"
    gemini.is_configured.return_value = False
    try:
        with (
            patch.object(worker_llm, "db_session", session),
            patch.object(worker_llm, "AnthropicProvider", return_value=provider),
            patch.object(worker_llm, "GeminiProvider", return_value=gemini),
        ):
            await worker_llm.generate_draft_for_entry(entry_id, **kwargs)
    finally:
        await engine.dispose()


async def _reload(db: AsyncSession, entry_id: uuid.UUID) -> Entry:
    result = await db.execute(
        select(Entry).where(Entry.id == entry_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _entry_with_picnic(
    db: AsyncSession, *, body: str | None, body_source: str = "llm", title: str | None = "Picnic",
    flagged: list[str] | None = None,
) -> Entry:
    user = await make_user(db)
    diary = await make_diary(db, user)
    entry = await make_entry(
        db, diary, entry_date=date(2026, 5, 19), title=title, body_markdown=body
    )
    entry.body_source = body_source
    entry.flagged_tokens = flagged
    await db.commit()
    await make_event(db, entry=entry, payload=_PICNIC)
    return entry


def _system_prompt(provider: MagicMock) -> str:
    return provider.generate.call_args.args[0]


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


class TestRegenerateSuggests:
    async def test_an_untouched_ai_draft_gets_a_fresh_draft_as_a_suggestion(
        self, db_session, db_url
    ):
        entry = await _entry_with_picnic(
            db_session, body="We had a picnic.", flagged=["Sandwiches"]
        )
        provider = _provider(_reply("A sunny picnic in the park."))

        await _run_draft(db_url, entry.id, provider, trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert _system_prompt(provider) == worker_llm.SYSTEM_PROMPT_EVENTS
        assert (after.title, after.body_markdown, after.body_source, after.flagged_tokens) == (
            "Picnic", "We had a picnic.", "llm", ["Sandwiches"]
        )
        assert after.suggested_title == "Picnic day"
        assert after.suggested_body_markdown == "A sunny picnic in the park."
        assert after.suggested_body_source == "llm"
        assert after.suggested_flagged_tokens == []
        assert after.suggested_at is not None

    async def test_the_parents_text_gets_a_blend_as_a_suggestion(self, db_session, db_url):
        entry = await _entry_with_picnic(
            db_session, body="She loved it.", body_source="user", title="Visit from Grandma"
        )
        provider = _provider(_reply("Grandma came to the picnic in the park."))

        await _run_draft(db_url, entry.id, provider, trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert _system_prompt(provider) == worker_llm.SYSTEM_PROMPT_HYBRID
        assert (after.title, after.body_markdown, after.body_source) == (
            "Visit from Grandma", "She loved it.", "user"
        )
        assert after.suggested_body_source == "llm_hybrid"
        # "Grandma" is only in the parent's title, which counts as a source.
        assert after.suggested_flagged_tokens == []

    async def test_a_flagged_word_stays_flagged_after_regenerate(self, db_session, db_url):
        entry = await _entry_with_picnic(
            db_session, body="We went to Disneyland.", flagged=["Disneyland"]
        )
        provider = _provider(_reply("We had a picnic, then Disneyland."))

        await _run_draft(db_url, entry.id, provider, trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert after.flagged_tokens == ["Disneyland"]
        assert after.suggested_flagged_tokens == ["Disneyland"]

    async def test_a_new_suggestion_replaces_the_pending_one(self, db_session, db_url):
        entry = await _entry_with_picnic(db_session, body="We had a picnic.")
        await _run_draft(db_url, entry.id, _provider(_reply("First take.")), trigger="regenerate")

        await _run_draft(db_url, entry.id, _provider(_reply("Second take.")), trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert after.suggested_body_markdown == "Second take."

    async def test_a_failed_regenerate_keeps_the_pending_suggestion(self, db_session, db_url):
        entry = await _entry_with_picnic(db_session, body="We had a picnic.")
        await _run_draft(db_url, entry.id, _provider(_reply("First take.")), trigger="regenerate")

        await _run_draft(db_url, entry.id, _provider(fail=True), trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert after.body_markdown == "We had a picnic."
        assert after.suggested_body_markdown == "First take."
        last = (
            await db_session.execute(
                select(LLMGeneration)
                .where(LLMGeneration.entry_id == entry.id)
                .order_by(LLMGeneration.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert last.status == "failed"


class TestWhenTheDraftGoesStraightIn:
    async def test_an_empty_entry_gets_its_first_draft_in_place(self, db_session, db_url):
        entry = await _entry_with_picnic(db_session, body=None, title=None)

        await _run_draft(
            db_url, entry.id, _provider(_reply("A picnic in the park.")), trigger="regenerate"
        )

        after = await _reload(db_session, entry.id)
        assert (after.body_markdown, after.body_source) == ("A picnic in the park.", "llm")
        assert after.suggested_body_markdown is None

    async def test_a_background_redraft_replaces_machine_only_text(self, db_session, db_url):
        entry = await _entry_with_picnic(db_session, body="Old draft.")

        await _run_draft(db_url, entry.id, _provider(_reply("New draft.")))

        after = await _reload(db_session, entry.id)
        assert after.body_markdown == "New draft."
        assert after.suggested_body_markdown is None

    async def test_a_background_redraft_of_the_parents_text_is_a_suggestion(
        self, db_session, db_url
    ):
        entry = await _entry_with_picnic(db_session, body="Our own words.", body_source="user")

        await _run_draft(db_url, entry.id, _provider(_reply("Blended words.")))

        after = await _reload(db_session, entry.id)
        assert after.body_markdown == "Our own words."
        assert after.suggested_body_markdown == "Blended words."

    async def test_text_edited_while_the_ai_works_survives(self, db_session, db_url):
        entry = await _entry_with_picnic(db_session, body=None, title=None)

        async def parent_types_meanwhile():
            await _set_columns(
                db_session, str(entry.id), body_markdown="edited meanwhile", body_source="user"
            )

        await _run_draft(
            db_url,
            entry.id,
            _provider(_reply("A picnic in the park."), before_reply=parent_types_meanwhile),
            trigger="regenerate",
        )

        after = await _reload(db_session, entry.id)
        assert after.body_markdown == "edited meanwhile"
        assert after.suggested_body_markdown == "A picnic in the park."

    async def test_machine_only_text_with_no_events_is_nothing_to_draft_from(
        self, db_session, db_url
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, user)
        entry = await make_entry(db_session, diary, body_markdown="An old AI draft.")
        provider = _provider(_reply("Reworded."))

        await _run_draft(db_url, entry.id, provider, trigger="regenerate")

        after = await _reload(db_session, entry.id)
        assert after.body_markdown == "An old AI draft."
        assert after.suggested_body_markdown is None
        assert provider.generate.call_count == 0
        gen = (
            await db_session.execute(
                select(LLMGeneration).where(LLMGeneration.entry_id == entry.id)
            )
        ).scalar_one()
        assert (gen.status, gen.error) == ("failed", "no_inputs")
