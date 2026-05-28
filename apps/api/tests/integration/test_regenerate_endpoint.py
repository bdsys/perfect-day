"""Integration tests: POST /v1/entries/{id}/regenerate endpoint + LLM worker round-trip.

These tests exercise the FastAPI endpoint, the Celery task (called directly, not via broker),
and the DB round-trip using the shared testcontainer fixtures from conftest.py.

Tests 20-22 from spec.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.workers.llm_providers import LLMPermanentError, LLMResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup(client: AsyncClient, email: str) -> tuple[str, dict, dict]:
    """Register a user, create a diary, return (token, auth_headers, diary_json)."""
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post(
            "/v1/diaries",
            json={"name": "Test Diary", "timezone": "UTC"},
            headers=auth,
        )
    ).json()
    return token, auth, diary


async def _create_entry(
    client: AsyncClient,
    diary_id: str,
    auth: dict,
    *,
    entry_date: str = "2026-05-19",
    body_markdown: str | None = None,
    title: str | None = None,
) -> dict:
    payload: dict = {"entry_date": entry_date}
    if body_markdown is not None:
        payload["body_markdown"] = body_markdown
    if title is not None:
        payload["title"] = title
    r = await client.post(f"/v1/diaries/{diary_id}/entries", json=payload, headers=auth)
    assert r.status_code == 201, f"create_entry failed: {r.text}"
    return r.json()


def _make_llm_result(raw: str, model: str = "claude-haiku-4-5-20251001") -> LLMResult:
    return LLMResult(raw_text=raw, input_tokens=100, output_tokens=50, model=model)


def _mock_provider(*, name: str, raw: str | None = None, fail: bool = False) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.is_configured.return_value = True
    if fail:
        p.generate = AsyncMock(side_effect=LLMPermanentError("mocked failure"))
    else:
        assert raw is not None
        p.generate = AsyncMock(return_value=_make_llm_result(raw))
    return p


def _make_db_session_patcher(db_url: str):
    """Return a context-manager factory that patches llm.db_session with a real DB session."""
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _patched_db_session():
        async with factory() as session:
            yield session
            await session.commit()

    return _patched_db_session, engine


# ---------------------------------------------------------------------------
# Test 20: regenerate endpoint returns last_generation field
# ---------------------------------------------------------------------------


class TestRegenerateReturnsLastGeneration:
    async def test_regenerate_returns_entry_with_last_generation_field(
        self, client: AsyncClient, db_url: str
    ):
        """POST /entries/{id}/regenerate → task runs (mocked provider) → GET entry returns
        last_generation with id, status, mode, created_at."""
        _, auth, diary = await _setup(client, "regen-20@example.com")

        # Entry with events only (mode=events)
        entry = await _create_entry(client, diary["id"], auth)

        _events_output = json.dumps({
            "title": "A Great Day",
            "title_facts_used": [],
            "body_markdown": "Nothing much happened.",
            "facts_used": [],
        })
        anthropic_p = _mock_provider(name="anthropic", raw=_events_output)
        gemini_p = MagicMock()
        gemini_p.name = "gemini"
        gemini_p.is_configured.return_value = False

        db_session_factory, engine = _make_db_session_patcher(db_url)

        import app.workers.llm as worker_llm
        import app.workers.tasks as worker_tasks

        try:
            with (
                patch.object(worker_llm, "db_session", db_session_factory),
                patch.object(worker_llm, "AnthropicProvider", return_value=anthropic_p),
                patch.object(worker_llm, "GeminiProvider", return_value=gemini_p),
                patch.object(worker_tasks.generate_entry_draft, "delay", new=MagicMock()),
            ):
                # Call regenerate endpoint — .delay is mocked so Celery is not invoked
                r = await client.post(f"/v1/entries/{entry['id']}/regenerate", headers=auth)
                assert r.status_code == 200

                # Run the LLM worker directly (simulating eager task execution)
                from app.workers.llm import generate_draft_for_entry
                await generate_draft_for_entry(uuid.UUID(entry["id"]))

            # GET the updated entry
            r2 = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
            assert r2.status_code == 200
            data = r2.json()
        finally:
            await engine.dispose()

        # Assert last_generation is populated
        assert data["last_generation"] is not None
        lg = data["last_generation"]
        assert "id" in lg
        assert "status" in lg
        assert "mode" in lg
        assert "created_at" in lg
        assert lg["status"] in ("success", "failed")
        assert lg["mode"] in ("events", "polish", "hybrid", "none")


# ---------------------------------------------------------------------------
# Test 21: polish mode sends <draft_body> to provider
# ---------------------------------------------------------------------------


class TestRegeneratePolishPassesDraftBody:
    async def test_regenerate_polish_mode_passes_draft_body_to_provider(
        self, client: AsyncClient, db_url: str
    ):
        """Entry with body text and no events → LLM worker uses polish mode; prompt
        sent to provider contains <draft_body>."""
        _, auth, diary = await _setup(client, "regen-21@example.com")

        draft_body = "Alice went to the park and played on the swings."
        entry = await _create_entry(
            client, diary["id"], auth, body_markdown=draft_body
        )

        captured_messages: list = []

        async def capture_generate(system_prompt, diary_context, entry_data):
            captured_messages.append((system_prompt, diary_context, entry_data))
            polish_output = json.dumps({
                "title": "Park Day",
                "body_markdown": "Alice played on the swings at the park.",
            })
            return _make_llm_result(polish_output)

        anthropic_p = MagicMock()
        anthropic_p.name = "anthropic"
        anthropic_p.is_configured.return_value = True
        anthropic_p.generate = AsyncMock(side_effect=capture_generate)

        gemini_p = MagicMock()
        gemini_p.name = "gemini"
        gemini_p.is_configured.return_value = False

        db_session_factory, engine = _make_db_session_patcher(db_url)
        import app.workers.llm as worker_llm

        try:
            with (
                patch.object(worker_llm, "db_session", db_session_factory),
                patch.object(worker_llm, "AnthropicProvider", return_value=anthropic_p),
                patch.object(worker_llm, "GeminiProvider", return_value=gemini_p),
            ):
                from app.workers.llm import generate_draft_for_entry
                await generate_draft_for_entry(uuid.UUID(entry["id"]))
        finally:
            await engine.dispose()

        # The entry_data argument passed to the provider must contain <draft_body>
        assert len(captured_messages) == 1, "Expected exactly one LLM call"
        _system, _ctx, entry_data = captured_messages[0]
        assert "<draft_body>" in entry_data, (
            f"Expected <draft_body> in entry_data; got:\n{entry_data}"
        )
        assert draft_body in entry_data, (
            "Expected the original draft body text to appear inside <draft_body>"
        )


# ---------------------------------------------------------------------------
# Test 22: failure does not overwrite body; last_generation reflects failure
# ---------------------------------------------------------------------------


class TestRegenerateFailurePreservesBody:
    async def test_regenerate_failure_does_not_overwrite_body(
        self, client: AsyncClient, db_url: str
    ):
        """Entry with body text, no events. Provider fails. After task runs:
        - body_markdown is unchanged
        - last_generation.status == 'failed'
        - last_generation.error is populated
        """
        _, auth, diary = await _setup(client, "regen-22@example.com")

        original_body = "This is my original diary entry."
        entry = await _create_entry(
            client, diary["id"], auth, body_markdown=original_body
        )

        anthropic_p = _mock_provider(name="anthropic", fail=True)
        gemini_p = MagicMock()
        gemini_p.name = "gemini"
        gemini_p.is_configured.return_value = False

        db_session_factory, engine = _make_db_session_patcher(db_url)
        import app.workers.llm as worker_llm

        try:
            with (
                patch.object(worker_llm, "db_session", db_session_factory),
                patch.object(worker_llm, "AnthropicProvider", return_value=anthropic_p),
                patch.object(worker_llm, "GeminiProvider", return_value=gemini_p),
            ):
                from app.workers.llm import generate_draft_for_entry
                await generate_draft_for_entry(uuid.UUID(entry["id"]))

            # GET the entry after the failed task
            r = await client.get(f"/v1/entries/{entry['id']}", headers=auth)
            assert r.status_code == 200
            data = r.json()
        finally:
            await engine.dispose()

        # Body must be unchanged
        assert data["body_markdown"] == original_body, (
            f"Expected body_markdown='{original_body}', got '{data['body_markdown']}'"
        )

        # last_generation must reflect the failure
        assert data["last_generation"] is not None
        lg = data["last_generation"]
        assert lg["status"] == "failed", f"Expected status='failed', got '{lg['status']}'"
        assert lg["error"] is not None and len(lg["error"]) > 0, (
            "Expected non-empty error on failed generation"
        )
        assert lg["mode"] == "polish", f"Expected mode='polish', got '{lg['mode']}'"
