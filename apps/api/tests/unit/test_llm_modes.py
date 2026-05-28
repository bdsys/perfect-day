"""Unit tests: generate_draft_for_entry — mode detection, body-preservation, body_source."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.llm_providers import LLMPermanentError, LLMResult, LLMTransientError

# ---------------------------------------------------------------------------
# Helpers — shared with test_llm_failover.py conventions
# ---------------------------------------------------------------------------

_EVENTS_OUTPUT = json.dumps({
    "title": "A Great Day",
    "title_facts_used": [1],
    "body_markdown": "Standup happened.",
    "facts_used": [1],
})

_POLISH_OUTPUT = json.dumps({
    "title": "Polished Title",
    "body_markdown": "Polished text goes here.",
})

_HYBRID_OUTPUT = json.dumps({
    "title": "Hybrid Title",
    "title_facts_used": [],
    "body_markdown": "Hybrid text combining draft and event.",
    "facts_used": [1],
})


def _llm_result(raw: str, model: str = "claude-haiku-4-5-20251001") -> LLMResult:
    return LLMResult(raw_text=raw, input_tokens=100, output_tokens=50, model=model)


def _make_provider(*, name: str, configured: bool = True, side_effects=None, result=None):
    p = MagicMock()
    p.name = name
    p.is_configured.return_value = configured
    if side_effects is not None:
        p.generate = AsyncMock(side_effect=side_effects)
    elif result is not None:
        p.generate = AsyncMock(return_value=result)
    else:
        p.generate = AsyncMock(return_value=_llm_result(_EVENTS_OUTPUT))
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


def _make_entry(
    *,
    body_markdown: str | None = None,
    events: list | None = None,
    title: str | None = None,
    body_source: str = "llm",
) -> tuple[uuid.UUID, MagicMock, MagicMock]:
    """Return (entry_id, entry, diary)."""
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
    entry.title = title
    entry.body_markdown = body_markdown
    entry.body_source = body_source
    entry.events = events if events is not None else []
    entry.enrichments = []

    return entry_id, entry, diary


def _db_context(entry: MagicMock, diary: MagicMock):
    """Build a context-manager mock that sequences the three DB calls made by
    generate_draft_for_entry:
      1. Load Entry with events+enrichments (first execute call)
      2. Load Diary (second execute call)
      3. Load Entry again for the write (third execute call)
    Returns (ctx, entry_update) — entry_update is the write-side entry mock.
    """
    entry_update = MagicMock()
    entry_update.id = entry.id
    entry_update.entry_date = entry.entry_date
    entry_update.body_markdown = entry.body_markdown  # preserve initial value
    entry_update.body_source = entry.body_source

    call_count = {"n": 0}

    async def fake_execute(_query):
        call_count["n"] += 1
        result = MagicMock()
        if call_count["n"] == 1:
            result.scalar_one_or_none.return_value = entry
        elif call_count["n"] == 2:
            result.scalar_one_or_none.return_value = diary
        else:
            result.scalar_one_or_none.return_value = entry_update
        return result

    db = MagicMock()
    db.execute = fake_execute
    db.add = MagicMock()

    saved_gens: list = []

    def capturing_add(obj):
        from app.models import LLMGeneration
        if isinstance(obj, LLMGeneration):
            saved_gens.append(obj)
        return None

    db.add = capturing_add

    class FakeCtx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            pass

    return FakeCtx(), entry_update, saved_gens


# ---------------------------------------------------------------------------
# Mode detection tests (10-12)
# ---------------------------------------------------------------------------


class TestModeDetection:
    @pytest.mark.asyncio
    async def test_mode_detection_events_only(self):
        """entry with events, body_markdown=None → build_prompt called with mode='events'."""
        entry_id, entry, diary = _make_entry(
            body_markdown=None,
            events=[_make_event()],
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_EVENTS_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, _ = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch(
                "app.workers.llm.build_prompt",
                wraps=__import__("app.workers.llm", fromlist=["build_prompt"]).build_prompt,
            ) as mock_bp,
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # build_prompt must have been called with mode="events"
        assert mock_bp.called
        _, kwargs = mock_bp.call_args
        assert kwargs.get("mode") == "events"

    @pytest.mark.asyncio
    async def test_mode_detection_body_only_picks_polish(self):
        """entry with body_markdown, no events → build_prompt called with mode='polish'."""
        entry_id, entry, diary = _make_entry(
            body_markdown="some typed text",
            events=[],
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_POLISH_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, _ = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch(
                "app.workers.llm.build_prompt",
                wraps=__import__("app.workers.llm", fromlist=["build_prompt"]).build_prompt,
            ) as mock_bp,
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert mock_bp.called
        _, kwargs = mock_bp.call_args
        assert kwargs.get("mode") == "polish"

    @pytest.mark.asyncio
    async def test_mode_detection_both_picks_hybrid(self):
        """entry with body AND events → build_prompt called with mode='hybrid'."""
        entry_id, entry, diary = _make_entry(
            body_markdown="Alice went to soccer.",
            events=[_make_event()],
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_HYBRID_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, _ = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch(
                "app.workers.llm.build_prompt",
                wraps=__import__("app.workers.llm", fromlist=["build_prompt"]).build_prompt,
            ) as mock_bp,
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert mock_bp.called
        _, kwargs = mock_bp.call_args
        assert kwargs.get("mode") == "hybrid"


# ---------------------------------------------------------------------------
# No-inputs test (13)
# ---------------------------------------------------------------------------


class TestNoInputs:
    @pytest.mark.asyncio
    async def test_no_inputs_does_not_overwrite_body(self):
        """No body AND no events → body_markdown stays None; LLMGeneration with mode='none',
        status='failed', error='no_inputs' is added."""
        entry_id, entry, diary = _make_entry(body_markdown=None, events=[])
        ctx, entry_update, saved_gens = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch(
                "app.workers.llm.AnthropicProvider",
                return_value=_make_provider(name="anthropic"),
            ),
            patch(
                "app.workers.llm.GeminiProvider",
                return_value=_make_provider(name="gemini", configured=False),
            ),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # Body must never be overwritten
        assert (
            entry_update.body_markdown is None
            or entry_update.body_markdown == entry.body_markdown
        )

        # A failed LLMGeneration row must have been added
        assert len(saved_gens) == 1
        gen = saved_gens[0]
        assert gen.mode == "none"
        assert gen.status == "failed"
        assert gen.error == "no_inputs"


# ---------------------------------------------------------------------------
# Body-preservation on failure (14-15)
# ---------------------------------------------------------------------------


class TestBodyPreservationOnFailure:
    @pytest.mark.asyncio
    async def test_polish_failure_does_not_overwrite_body(self):
        """Polish mode: provider raises → entry.body_markdown is unchanged; failed gen row added."""
        original_body = "my typed text"
        entry_id, entry, diary = _make_entry(
            body_markdown=original_body,
            events=[],
            body_source="llm_polished",
        )

        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMPermanentError("503")] * 3,
        )
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, saved_gens = _db_context(entry, diary)
        # Ensure entry_update preserves initial body
        entry_update.body_markdown = original_body

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # Body must not have been changed by the worker
        assert entry_update.body_markdown == original_body

        # A failed generation row must exist
        assert len(saved_gens) == 1
        gen = saved_gens[0]
        assert gen.status == "failed"
        assert gen.mode == "polish"

    @pytest.mark.asyncio
    async def test_hybrid_failure_does_not_overwrite_body(self):
        """Hybrid mode: provider returns invalid JSON → entry.body_markdown unchanged."""
        original_body = "Alice had a great day."
        entry_id, entry, diary = _make_entry(
            body_markdown=original_body,
            events=[_make_event()],
        )
        garbage_result = _llm_result("not json at all !!!", model="claude-haiku-4-5-20251001")
        anthropic_p = _make_provider(name="anthropic", result=garbage_result)
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, saved_gens = _db_context(entry, diary)
        entry_update.body_markdown = original_body

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_markdown == original_body

        assert len(saved_gens) == 1
        gen = saved_gens[0]
        assert gen.status == "failed"
        assert gen.mode == "hybrid"


# ---------------------------------------------------------------------------
# Events-only fallback preserved (16)
# ---------------------------------------------------------------------------


class TestEventsFallback:
    @pytest.mark.asyncio
    async def test_events_only_failure_still_runs_deterministic_fallback(self):
        """events-only mode: all providers fail → _build_fallback_body runs; body non-empty."""
        entry_id, entry, diary = _make_entry(
            body_markdown=None,
            events=[_make_event("Soccer practice")],
        )
        anthropic_p = _make_provider(
            name="anthropic",
            side_effects=[LLMTransientError("5xx")] * 3,
        )
        gemini_p = _make_provider(
            name="gemini",
            side_effects=[LLMTransientError("5xx")] * 3,
        )
        ctx, entry_update, _ = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # Fallback must have written something non-empty
        assert entry_update.body_markdown is not None
        assert len(entry_update.body_markdown) > 0
        # Fallback body is bullet-list format
        first_line = entry_update.body_markdown.strip().splitlines()[0]
        assert first_line.startswith("-")
        assert entry_update.body_source == "fallback"


# ---------------------------------------------------------------------------
# Success path: body_source and mode on LLMGeneration (17-18)
# ---------------------------------------------------------------------------


class TestSuccessBodySource:
    @pytest.mark.asyncio
    async def test_polish_success_sets_body_source_llm_polished_and_mode(self):
        """Polish success: entry.body_source == 'llm_polished';
        LLMGeneration mode='polish', status='success'."""
        entry_id, entry, diary = _make_entry(
            body_markdown="Draft text.",
            events=[],
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_POLISH_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, saved_gens = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm_polished"
        assert len(saved_gens) == 1
        gen = saved_gens[0]
        assert gen.mode == "polish"
        assert gen.status == "success"

    @pytest.mark.asyncio
    async def test_hybrid_success_sets_body_source_llm_hybrid_and_mode(self):
        """Hybrid success: entry.body_source == 'llm_hybrid';
        LLMGeneration mode='hybrid', status='success'."""
        entry_id, entry, diary = _make_entry(
            body_markdown="Alice went to soccer.",
            events=[_make_event("Soccer practice")],
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_HYBRID_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, saved_gens = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        assert entry_update.body_source == "llm_hybrid"
        assert len(saved_gens) == 1
        gen = saved_gens[0]
        assert gen.mode == "hybrid"
        assert gen.status == "success"


# ---------------------------------------------------------------------------
# Title preservation (19)
# ---------------------------------------------------------------------------


class TestPolishTitlePreservation:
    @pytest.mark.asyncio
    async def test_polish_preserves_user_title_when_non_empty(self):
        """Polish mode with non-empty title: prompt sent to provider contains
        'CURRENT_TITLE: My Title'."""
        entry_id, entry, diary = _make_entry(
            body_markdown="Alice had fun.",
            events=[],
            title="My Title",
        )
        anthropic_p = _make_provider(name="anthropic", result=_llm_result(_POLISH_OUTPUT))
        gemini_p = _make_provider(name="gemini", configured=False)
        ctx, entry_update, _ = _db_context(entry, diary)

        with (
            patch("app.workers.llm.db_session", return_value=ctx),
            patch("app.workers.llm.AnthropicProvider", return_value=anthropic_p),
            patch("app.workers.llm.GeminiProvider", return_value=gemini_p),
        ):
            from app.workers.llm import generate_draft_for_entry
            await generate_draft_for_entry(entry_id)

        # The third argument to provider.generate is the per-entry data message
        assert anthropic_p.generate.called
        _, positional_args, _ = anthropic_p.generate.mock_calls[0]
        # positional_args: (system_prompt, diary_context, entry_data + user_message_extra)
        entry_data_arg = positional_args[2]
        assert "CURRENT_TITLE: My Title" in entry_data_arg
