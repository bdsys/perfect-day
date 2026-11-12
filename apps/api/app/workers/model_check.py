"""Weekly check that the configured drafting model is still the one to use.

No model id follows new releases automatically, and drafts about a child
should not change model untested. So the model is pinned in a setting and this
task only warns; acting on a warning is manual: change ANTHROPIC_MODEL, then
run `make test-live`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import structlog

from app.core.config import get_settings
from app.workers.celery_app import celery_app
from app.workers.utils import run_sync

log = structlog.get_logger()

# The Models API reports the epoch when it does not know a release date.
_UNKNOWN_RELEASE_DATE = datetime(1971, 1, 1, tzinfo=UTC)


class ModelWarning(NamedTuple):
    code: str  # "anthropic_model_missing" | "anthropic_newer_opus_available"
    model: str


def model_warnings(configured: str, models: list[Any]) -> list[ModelWarning]:
    """Compare the configured model with the API's list (objects with id, created_at).

    Warns when the configured id is not listed, and for each Opus released
    after it, newest first.
    """
    released = {m.id: m.created_at for m in models}
    if configured not in released:
        return [ModelWarning("anthropic_model_missing", configured)]

    configured_date = released[configured]
    newer = [
        m
        for m in models
        if m.id.startswith("claude-opus-")
        and m.created_at > _UNKNOWN_RELEASE_DATE
        and m.created_at > configured_date
    ]
    newer.sort(key=lambda m: m.created_at, reverse=True)
    return [ModelWarning("anthropic_newer_opus_available", m.id) for m in newer]


@celery_app.task(name="app.workers.model_check.check_anthropic_model")
def check_anthropic_model() -> None:
    run_sync(_check_anthropic_model())


async def _check_anthropic_model() -> list[ModelWarning]:
    import anthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        return []

    async with anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
        models = [m async for m in client.models.list(limit=100)]

    warnings = model_warnings(settings.anthropic_model, models)
    for warning in warnings:
        log.warning(warning.code, configured=settings.anthropic_model, model=warning.model)
    if not warnings:
        log.info("anthropic_model_current", configured=settings.anthropic_model)
    return warnings
