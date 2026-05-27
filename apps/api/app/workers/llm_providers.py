"""LLM provider abstraction: Anthropic (primary) and Gemini (fallback)."""

from __future__ import annotations

from typing import NamedTuple, Protocol, runtime_checkable

import structlog

from app.core.config import get_settings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result and error types
# ---------------------------------------------------------------------------


class LLMResult(NamedTuple):
    raw_text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str


class LLMTransientError(Exception):
    """5xx / rate-limit / network error — caller may retry or failover."""


class LLMPermanentError(Exception):
    """4xx auth / bad request — caller should NOT retry (config bug)."""


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    async def generate(
        self,
        system: str,
        diary_context: str,
        entry_data: str,
    ) -> LLMResult: ...


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------

_ANTHROPIC_PRIMARY_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider:
    name = "anthropic"

    def is_configured(self) -> bool:
        return bool(get_settings().anthropic_api_key)

    async def generate(self, system: str, diary_context: str, entry_data: str) -> LLMResult:
        import anthropic

        settings = get_settings()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        try:
            response = client.messages.create(
                model=_ANTHROPIC_PRIMARY_MODEL,
                max_tokens=1024,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": diary_context,
                                "cache_control": {"type": "ephemeral"},
                            },
                            {"type": "text", "text": entry_data},
                        ],
                    }
                ],  # type: ignore[list-item]  # SDK types don't model cache_control blocks
            )
        except anthropic.AuthenticationError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.BadRequestError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.APIError as e:
            raise LLMTransientError(str(e)) from e

        raw = response.content[0].text  # type: ignore[union-attr]
        return LLMResult(
            raw_text=raw,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            model=_ANTHROPIC_PRIMARY_MODEL,
        )


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------


class GeminiProvider:
    name = "gemini"

    def is_configured(self) -> bool:
        return bool(get_settings().gemini_api_key)

    async def generate(self, system: str, diary_context: str, entry_data: str) -> LLMResult:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        settings = get_settings()
        model_id = settings.gemini_model
        client = genai.Client(api_key=settings.gemini_api_key)

        contents = diary_context + "\n\n" + entry_data

        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": 2048,
        }
        # 2.5 Flash supports thinking_budget=0 to disable thinking entirely.
        # 2.5 Pro does not — its minimum is 128 and passing 0 returns HTTP 400.
        # Conditioning on "flash" covers gemini-2.5-flash and gemini-2.5-flash-lite;
        # Pro and unknown models get the API's default (dynamic thinking).
        if "flash" in model_id.lower():
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)

        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except genai_errors.APIError as e:
            # 401/403 → permanent (bad key); 429/5xx → transient
            if hasattr(e, "code") and e.code in (401, 403):
                raise LLMPermanentError(str(e)) from e
            raise LLMTransientError(str(e)) from e
        except Exception as e:
            raise LLMTransientError(str(e)) from e

        raw = response.text or ""
        usage = response.usage_metadata if hasattr(response, "usage_metadata") else None
        return LLMResult(
            raw_text=raw,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            model=model_id,
        )
