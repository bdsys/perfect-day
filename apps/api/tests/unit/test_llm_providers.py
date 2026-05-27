"""Unit tests: LLM provider abstraction — error mapping, is_configured."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.llm_providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMPermanentError,
    LLMResult,
    LLMTransientError,
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


class TestAnthropicProviderGenerate:
    def _mock_response(self, text: str = _DEFAULT_ANTHROPIC_RESPONSE):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 50
        return resp

    @pytest.mark.asyncio
    async def test_success_returns_llm_result(self):

        mock_resp = self._mock_response("hello")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("anthropic.Anthropic", return_value=mock_client),
        ):
            mock_settings.return_value.anthropic_api_key = "sk-ant-abc"
            result = await AnthropicProvider().generate("sys", "ctx", "data")

        assert isinstance(result, LLMResult)
        assert result.raw_text == "hello"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert "claude" in result.model

    @pytest.mark.asyncio
    async def test_api_error_raises_transient(self):
        import anthropic

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APIError(
            message="server error", request=MagicMock(), body=None
        )

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("anthropic.Anthropic", return_value=mock_client),
        ):
            mock_settings.return_value.anthropic_api_key = "sk-ant-abc"
            with pytest.raises(LLMTransientError):
                await AnthropicProvider().generate("sys", "ctx", "data")

    @pytest.mark.asyncio
    async def test_authentication_error_raises_permanent(self):
        import anthropic

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.AuthenticationError(
            message="invalid key", response=MagicMock(), body=None
        )

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("anthropic.Anthropic", return_value=mock_client),
        ):
            mock_settings.return_value.anthropic_api_key = "bad-key"
            with pytest.raises(LLMPermanentError):
                await AnthropicProvider().generate("sys", "ctx", "data")

    @pytest.mark.asyncio
    async def test_bad_request_error_raises_permanent(self):
        import anthropic

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.BadRequestError(
            message="bad request", response=MagicMock(), body=None
        )

        with (
            patch("app.workers.llm_providers.get_settings") as mock_settings,
            patch("anthropic.Anthropic", return_value=mock_client),
        ):
            mock_settings.return_value.anthropic_api_key = "sk-ant-abc"
            with pytest.raises(LLMPermanentError):
                await AnthropicProvider().generate("sys", "ctx", "data")


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
