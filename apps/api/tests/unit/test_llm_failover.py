"""Unit tests: LLM provider failover orchestration in generate_draft_for_entry."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.llm_providers import LLMPermanentError, LLMResult, LLMTransientError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_LLM_OUTPUT = json.dumps({
    "title": "A Great Day",
    "title_facts_used": [1],
    "body_markdown": "Today there was a Standup.",
    "facts_used": [1],
})

_VALID_LLM_RESULT = LLMResult(
    raw_text=_VALID_LLM_OUTPUT,
    input_tokens=100,
    output_tokens=50,
    model="claude-haiku-4-5-20251001",
)

_GEMINI_RESULT = LLMResult(
    raw_text=_VALID_LLM_OUTPUT,
    input_tokens=80,
    output_tokens=40,
    model="gemini-2.5-pro",
)


def _make_provider(*, name: str, configured: bool = True, side_effects=None, result=None):
    """Build a mock LLMProvider."""
    p = MagicMock()
    p.name = name
    p.is_configured.return_value = configured
    if side_effects is not None:
        p.generate = AsyncMock(side_effect=side_effects)
    elif result is not None:
        p.generate = AsyncMock(return_value=result)
    else:
        p.generate = AsyncMock(return_value=_VALID_LLM_RESULT)
    return p


def _make_event(summary: str = "Standup") -> MagicMock:
    ev = MagicMock()
    ev.source = "google_calendar"
    ev.occurred_at = datetime(2026, 5, 19, 9, 0, tzinfo=UTC)
    ev.payload = {
        "summary": summary,
        "start": {"dateTime": "2026-05-19T09:00:00-07:00"},
        "end": {"dateTime": "2026-05-19T09:30:00-07:00"},
    }
    return ev


def _make_entry_and_diary():
    entry_id = uuid.uuid4()
    diary = MagicMock()
    diary.id = uuid.uuid4()
    diary.subject_name = "Alice"
    diary.subject_relation = "child"
    diary.voice_override = None
    diary.tone_hint = "warm"
    diary.timezone = "America/Chicago"

    entry = MagicMock()
    entry.id = entry_id
    entry.diary_id = diary.id
    entry.status = "draft"
    entry.entry_date = date(2026, 5, 19)
    entry.entry_end_date = None
    entry.events = [_make_event()]
    entry.enrichments = []

    return entry_id, entry, diary


# ---------------------------------------------------------------------------
# DB session mock: first call loads entry+diary, second call saves result
# ---------------------------------------------------------------------------

def _db_context(entry, diary):
    """Return a context-manager mock that returns entry (with events) on first
    execute and a fresh entry_update on the second execute."""
    entry_update = MagicMock()
    entry_update.id = entry.id
    entry_update.entry_date = entry.entry_date

    call_count = {"n": 0}

    async def fake_execute(_query):
        call_count["n"] += 1
        result = MagicMock()
        if call_count["n"] == 1:
            # First call: load entry with events + enrichments
            result.scalar_one_or_none.return_value = entry
        elif call_count["n"] == 2:
            # Second call: load diary
            result.scalar_one_or_none.return_value = diary
        else:
            # Third call: load entry_update for save
            result.scalar_one_or_none.return_value = entry_update
        return result

    db = MagicMock()
    db.execute = fake_execute
    db.add = MagicMock()

    class FakeCtx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            pass

    return FakeCtx(), entry_update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderFailover:
    @pytest.mark.asyncio
    async def test_anthropic_success_no_gemini_call(self):
        """Anthropic succeeds → Gemini never called."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(name="anthropic", result=_VALID_LLM_RESULT)
        gemini_p = _make_provider(name="gemini")

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch(
                "app.workers.llm.AnthropicProvider", return_value=anthropic_p
            ),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm"
        assert entry_update.title == "A Great Day"
        gemini_p.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_anthropic_transient_3x_falls_over_to_gemini(self):
        """Anthropic raises LLMTransientError 3 times → Gemini called, succeeds."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMTransientError("5xx")] * 3,
        )
        gemini_p = _make_provider(name="gemini", result=_GEMINI_RESULT)

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm"
        assert entry_update.title == "A Great Day"
        assert anthropic_p.generate.call_count == 3
        assert gemini_p.generate.call_count == 1

    @pytest.mark.asyncio
    async def test_anthropic_permanent_error_immediately_falls_over_to_gemini(self):
        """Anthropic raises LLMPermanentError once → switches to Gemini immediately (no retries)."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMPermanentError("bad key")],
        )
        gemini_p = _make_provider(name="gemini", result=_GEMINI_RESULT)

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert anthropic_p.generate.call_count == 1  # no retries
        assert gemini_p.generate.call_count == 1
        assert entry_update.body_source == "llm"

    @pytest.mark.asyncio
    async def test_both_providers_fail_deterministic_fallback(self):
        """Both providers fail → deterministic _build_fallback_body runs, body_source='fallback'."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMTransientError("5xx")] * 3,
        )
        gemini_p = _make_provider(
            name="gemini",
            side_effects=[LLMTransientError("5xx")] * 3,
        )

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "fallback"
        # Fallback title for a single event with a summary is the summary itself
        assert entry_update.title == "Standup"

    @pytest.mark.asyncio
    async def test_gemini_not_configured_only_anthropic_runs(self):
        """Gemini not configured → not in provider list; behavior identical to pre-refactor."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(name="anthropic", result=_VALID_LLM_RESULT)
        gemini_p = _make_provider(name="gemini", configured=False)

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm"
        gemini_p.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_citation_failure_does_not_trigger_provider_failover(self):
        """Citation validation failure retries within the same provider, not across providers."""
        entry_id, entry, diary = _make_entry_and_diary()

        # Return a result with an out-of-range facts_used index — will fail citation validation
        bad_output = json.dumps({
            "title": "Bad",
            "title_facts_used": [99],   # index 99 doesn't exist
            "body_markdown": "body",
            "facts_used": [99],
        })
        bad_result = LLMResult(
            raw_text=bad_output,
            input_tokens=10,
            output_tokens=10,
            model="claude-haiku-4-5-20251001",
        )
        anthropic_p = _make_provider(name="anthropic", result=bad_result)
        gemini_p = _make_provider(name="gemini", result=_GEMINI_RESULT)

        ctx, entry_update = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # All 3 attempts within Anthropic, then fallback (no usable result)
        assert anthropic_p.generate.call_count == 3
        # Gemini should NOT be tried for citation failures
        gemini_p.generate.assert_not_called()
        assert entry_update.body_source == "fallback"

    @pytest.mark.asyncio
    async def test_parse_failure_llm_generation_row_has_real_model_and_tokens(self):
        """When JSON parsing fails, LLMGeneration.model and token counts come from the
        provider result, not the provider.name fallback."""
        entry_id, entry, diary = _make_entry_and_diary()

        # Provider returns a valid HTTP response but with unparseable content
        garbage_result = LLMResult(
            raw_text="Sorry, I cannot help with that.",  # no JSON object at all
            input_tokens=55,
            output_tokens=12,
            model="gemini-2.5-pro",
        )
        anthropic_p = _make_provider(
            name="anthropic", side_effects=[LLMPermanentError("401")]
        )
        gemini_p = _make_provider(name="gemini", result=garbage_result)

        ctx, entry_update = _db_context(entry, diary)
        saved_gen = None

        original_add = ctx.__class__.__aenter__

        async def _capture_add(self):
            db = await original_add(self)
            original_db_add = db.add

            def capturing_add(obj):
                nonlocal saved_gen
                from app.models import LLMGeneration
                if isinstance(obj, LLMGeneration):
                    saved_gen = obj
                return original_db_add(obj)

            db.add = capturing_add
            return db

        ctx.__class__.__aenter__ = _capture_add

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "fallback"
        assert saved_gen is not None
        assert saved_gen.model == "gemini-2.5-pro", (
            f"Expected real model id, got '{saved_gen.model}'"
        )
        assert saved_gen.input_tokens == 55
        assert saved_gen.output_tokens == 12
        assert saved_gen.status == "failed"
        assert "no JSON" in (saved_gen.error or "")

    @pytest.mark.asyncio
    async def test_fallback_bumps_updated_at(self):
        """Both providers fail → fallback branch sets entry_update.updated_at."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMTransientError("5xx")] * 3,
        )
        gemini_p = _make_provider(
            name="gemini",
            side_effects=[LLMTransientError("5xx")] * 3,
        )

        ctx, entry_update = _db_context(entry, diary)

        before = datetime.now(UTC)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "fallback"
        assert hasattr(entry_update, "updated_at"), "updated_at was never set"
        assert entry_update.updated_at >= before, (
            f"updated_at ({entry_update.updated_at}) should be >= call start ({before})"
        )

    @pytest.mark.asyncio
    async def test_llm_success_bumps_updated_at(self):
        """LLM success path sets entry_update.updated_at so frontend polling detects the write."""
        entry_id, entry, diary = _make_entry_and_diary()
        anthropic_p = _make_provider(name="anthropic", result=_VALID_LLM_RESULT)
        gemini_p = _make_provider(name="gemini")

        ctx, entry_update = _db_context(entry, diary)

        before = datetime.now(UTC)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm"
        assert hasattr(entry_update, "updated_at"), "updated_at was never set"
        assert entry_update.updated_at >= before, (
            f"updated_at ({entry_update.updated_at}) should be >= call start ({before})"
        )
