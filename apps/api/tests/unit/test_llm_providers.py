"""Unit tests: LLM provider abstraction — error mapping, is_configured."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.llm_providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMPermanentError,
    LLMResult,
    LLMTransientError,
    LLMUnusableResponseError,
)

# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class TestAnthropicProviderIsConfigured:
    def test_configured_when_key_set(self):
        with patch("app.workers.llm_providers.get_settings") as mock_settings:
            mock_settings.return_value.anthropic_api_key = "sk-ant-abc"
            assert AnthropicProvider().is_configured() is True

    def test_not_configured_when_key_empty(self):
        with patch("app.workers.llm_providers.get_settings") as mock_settings:
            mock_settings.return_value.anthropic_api_key = ""
            assert AnthropicProvider().is_configured() is False


_DEFAULT_ANTHROPIC_RESPONSE = (
    '{"title":"T","body_markdown":"B","facts_used":[1],"title_facts_used":[1]}'
)


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _thinking() -> SimpleNamespace:
    # A thinking block has no `text` attribute at all.
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def _anthropic_response(
    content: list | None = None,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5-5",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[_text(_DEFAULT_ANTHROPIC_RESPONSE)] if content is None else content,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class _FakeAnthropic:
    """Stands in for anthropic.AsyncAnthropic; records the request it was sent."""

    def __init__(self, response=None, error: Exception | None = None):
        self.create = AsyncMock(return_value=response, side_effect=error)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


async def _generate(fake: _FakeAnthropic, *, model: str = "claude-opus-5-5") -> LLMResult:
    with (
        patch("app.workers.llm_providers.get_settings") as mock_settings,
        patch("anthropic.AsyncAnthropic", return_value=fake),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-abc"
        mock_settings.return_value.anthropic_model = model
        return await AnthropicProvider().generate("sys", "ctx", "data")


class TestAnthropicProviderGenerate:
    async def test_success_returns_text_tokens_and_the_model_that_answered(self):
        fake = _FakeAnthropic(_anthropic_response([_text("hello")], model="claude-opus-5-5"))

        result = await _generate(fake)

        assert result == LLMResult(
            raw_text="hello", input_tokens=100, output_tokens=50, model="claude-opus-5-5"
        )

    async def test_reply_that_opens_with_a_thinking_block_is_parsed(self):
        fake = _FakeAnthropic(_anthropic_response([_thinking(), _text("the draft")]))

        result = await _generate(fake)

        assert result.raw_text == "the draft"

    async def test_model_comes_from_the_setting(self):
        fake = _FakeAnthropic(_anthropic_response())

        await _generate(fake, model="claude-some-other-model")

        assert fake.create.await_args.kwargs["model"] == "claude-some-other-model"

    async def test_request_leaves_room_for_thinking_and_opts_into_refusal_fallback(self):
        fake = _FakeAnthropic(_anthropic_response())

        await _generate(fake)

        sent = fake.create.await_args.kwargs
        assert sent["max_tokens"] == 16000
        assert sent["output_config"] == {"effort": "medium"}
        assert sent["betas"] == ["server-side-fallback-2026-07-01"]
        assert sent["extra_body"] == {"fallbacks": "default"}

    @pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
    async def test_cut_off_or_refused_reply_is_not_returned_as_a_draft(self, stop_reason):
        fake = _FakeAnthropic(
            _anthropic_response([_text('{"title": "Half a')], stop_reason=stop_reason)
        )

        with pytest.raises(LLMUnusableResponseError, match=stop_reason):
            await _generate(fake)

    async def test_reply_with_no_text_block_is_not_returned_as_a_draft(self):
        fake = _FakeAnthropic(_anthropic_response([_thinking()]))

        with pytest.raises(LLMUnusableResponseError):
            await _generate(fake)

    async def test_api_error_raises_transient(self):
        import anthropic

        fake = _FakeAnthropic(
            error=anthropic.APIError(message="server error", request=MagicMock(), body=None)
        )
        with pytest.raises(LLMTransientError):
            await _generate(fake)

    async def test_authentication_error_raises_permanent(self):
        import anthropic

        fake = _FakeAnthropic(
            error=anthropic.AuthenticationError(
                message="invalid key", response=MagicMock(), body=None
            )
        )
        with pytest.raises(LLMPermanentError):
            await _generate(fake)

    async def test_bad_request_error_raises_permanent(self):
        import anthropic

        fake = _FakeAnthropic(
            error=anthropic.BadRequestError(message="bad request", response=MagicMock(), body=None)
        )
        with pytest.raises(LLMPermanentError):
            await _generate(fake)


# ---------------------------------------------------------------------------
# GeminiProvider
# ---------------------------------------------------------------------------


class TestGeminiProviderIsConfigured:
    def test_configured_when_key_set(self):
        with patch("app.workers.llm_providers.get_settings") as mock_settings:
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            assert GeminiProvider().is_configured() is True

    def test_not_configured_when_key_empty(self):
        with patch("app.workers.llm_providers.get_settings") as mock_settings:
            mock_settings.return_value.gemini_api_key = ""
            assert GeminiProvider().is_configured() is False


class TestGeminiProviderGenerate:
    def _mock_genai(self, text: str = "hello", *, error=None):
        mock_response = MagicMock()
        mock_response.text = text
        mock_response.usage_metadata.prompt_token_count = 80
        mock_response.usage_metadata.candidates_token_count = 40

        mock_aio = MagicMock()
        if error:
            mock_aio.models.generate_content = AsyncMock(side_effect=error)
        else:
            mock_aio.models.generate_content = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.aio = mock_aio
        return mock_client

    @pytest.mark.asyncio
    async def test_success_returns_llm_result(self):
        mock_client = self._mock_genai("hello")

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-pro"
            result = await GeminiProvider().generate("sys", "ctx", "data")

        assert isinstance(result, LLMResult)
        assert result.raw_text == "hello"
        assert result.model == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_401_api_error_raises_permanent(self):
        from google.genai import errors as genai_errors

        err = genai_errors.APIError(401, "Unauthorized")
        mock_client = self._mock_genai(error=err)

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "bad"
            mock_settings.return_value.gemini_model = "gemini-2.5-pro"
            with pytest.raises(LLMPermanentError):
                await GeminiProvider().generate("sys", "ctx", "data")

    @pytest.mark.asyncio
    async def test_500_api_error_raises_transient(self):
        from google.genai import errors as genai_errors

        err = genai_errors.APIError(500, "Internal server error")
        mock_client = self._mock_genai(error=err)

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-pro"
            with pytest.raises(LLMTransientError):
                await GeminiProvider().generate("sys", "ctx", "data")

    @pytest.mark.asyncio
    async def test_generic_exception_raises_transient(self):
        mock_client = self._mock_genai(error=ConnectionError("network"))

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-pro"
            with pytest.raises(LLMTransientError):
                await GeminiProvider().generate("sys", "ctx", "data")

    @pytest.mark.asyncio
    async def test_model_id_from_settings(self):
        mock_client = self._mock_genai("hello")

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-flash"
            result = await GeminiProvider().generate("sys", "ctx", "data")

        assert result.model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_thinking_disabled_for_flash(self):
        """generate() passes thinking_budget=0 for flash models (supports disabling thinking)."""
        from google.genai import types as genai_types

        mock_client = self._mock_genai("hello")

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-flash"
            await GeminiProvider().generate("sys", "ctx", "data")

        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert isinstance(config.thinking_config, genai_types.ThinkingConfig)
        assert config.thinking_config.thinking_budget == 0

    @pytest.mark.asyncio
    async def test_thinking_default_for_pro(self):
        """generate() does NOT set thinking_config for pro models (budget=0 returns 400)."""
        mock_client = self._mock_genai("hello")

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("google.genai.Client", return_value=mock_client),
        ):
            mock_settings.return_value.gemini_api_key = "AIzaSy-abc"
            mock_settings.return_value.gemini_model = "gemini-2.5-pro"
            await GeminiProvider().generate("sys", "ctx", "data")

        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert config.thinking_config is None
