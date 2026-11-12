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


class LLMUnusableResponseError(Exception):
    """The model answered, but not with a draft: it was cut off, or it declined.

    Not retried and not sent to another provider. The caller records a failed
    generation and leaves the entry as it was.
    """


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

# Thinking happens before the text and counts against max_tokens, so the cap
# has to cover both. A draft itself is a few hundred tokens.
_ANTHROPIC_MAX_TOKENS = 16000
# If the model declines a request, the API re-runs it once on Anthropic's
# recommended substitute inside the same call. The scalar "default" form is
# sent through extra_body: the SDK types only the list form.
_REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_UNUSABLE_STOP_REASONS = frozenset({"max_tokens", "refusal"})


class AnthropicProvider:
    name = "anthropic"

    def is_configured(self) -> bool:
        return bool(get_settings().anthropic_api_key)

    async def generate(self, system: str, diary_context: str, entry_data: str) -> LLMResult:
        import anthropic

        settings = get_settings()
        model = settings.anthropic_model

        try:
            async with anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
                response = await client.beta.messages.create(
                    model=model,
                    max_tokens=_ANTHROPIC_MAX_TOKENS,
                    # The model's own default, stated so a change of default
                    # upstream cannot change drafts unnoticed.
                    output_config={"effort": "medium"},
                    betas=[_REFUSAL_FALLBACK_BETA],
                    extra_body={"fallbacks": "default"},
                    system=system,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": diary_context,
                                    # Has no effect today: this block is below the
                                    # minimum cacheable size (review finding L-2).
                                    "cache_control": {"type": "ephemeral"},
                                },
                                {"type": "text", "text": entry_data},
                            ],
                        }
                    ],
                )
        except anthropic.AuthenticationError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.BadRequestError as e:
            raise LLMPermanentError(str(e)) from e
        except anthropic.APIError as e:
            raise LLMTransientError(str(e)) from e

        if response.stop_reason in _UNUSABLE_STOP_REASONS:
            raise LLMUnusableResponseError(f"stop_reason={response.stop_reason}")
        # The reply opens with a thinking block; the draft is the first text block.
        raw = next((block.text for block in response.content if block.type == "text"), None)
        if raw is None:
            raise LLMUnusableResponseError("no text block in response")

        return LLMResult(
            raw_text=raw,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            # The model that answered, which is the substitute after a refusal fallback.
            model=response.model or model,
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
