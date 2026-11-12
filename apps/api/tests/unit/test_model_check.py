"""Unit tests: the weekly check that the configured drafting model is still current."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.workers.model_check import ModelWarning, _check_anthropic_model, model_warnings


def _model(model_id: str, year: int, month: int) -> SimpleNamespace:
    return SimpleNamespace(id=model_id, created_at=datetime(year, month, 1, tzinfo=UTC))


CATALOGUE = [
    _model("claude-opus-5-5", 2026, 9),
    _model("claude-opus-5", 2026, 5),
    _model("claude-sonnet-5-5", 2026, 10),
    _model("claude-haiku-5-5", 2026, 10),
]


class TestModelWarnings:
    def test_current_opus_raises_nothing(self):
        assert model_warnings("claude-opus-5-5", CATALOGUE) == []

    def test_newer_sonnet_or_haiku_is_not_a_reason_to_warn(self):
        # Both are newer than Opus 5.5 in CATALOGUE and neither is an Opus.
        assert model_warnings("claude-opus-5-5", CATALOGUE) == []

    def test_model_no_longer_listed(self):
        assert model_warnings("claude-opus-4-1", CATALOGUE) == [
            ModelWarning("anthropic_model_missing", "claude-opus-4-1")
        ]

    def test_newer_opus_is_named(self):
        catalogue = [*CATALOGUE, _model("claude-opus-6", 2027, 2)]

        assert model_warnings("claude-opus-5-5", catalogue) == [
            ModelWarning("anthropic_newer_opus_available", "claude-opus-6")
        ]

    def test_older_opus_configured_reports_every_newer_one_newest_first(self):
        assert model_warnings("claude-opus-5", [*CATALOGUE, _model("claude-opus-6", 2027, 2)]) == [
            ModelWarning("anthropic_newer_opus_available", "claude-opus-6"),
            ModelWarning("anthropic_newer_opus_available", "claude-opus-5-5"),
        ]

    def test_model_with_unknown_release_date_is_not_treated_as_newer(self):
        # The API uses the epoch when it does not know the release date.
        undated = SimpleNamespace(id="claude-opus-9", created_at=datetime(1970, 1, 1, tzinfo=UTC))

        assert model_warnings("claude-opus-5-5", [*CATALOGUE, undated]) == []


class _FakeModels:
    def __init__(self, models):
        self._models = models

    def list(self, **_kwargs):
        async def _pages():
            for model in self._models:
                yield model

        return _pages()


class _FakeAnthropic:
    def __init__(self, models):
        self.models = _FakeModels(models)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class TestCheckTask:
    async def _run(self, *, key: str, model: str, catalogue) -> list[ModelWarning]:
        with (
            patch("app.workers.model_check.get_settings") as settings,
            patch("anthropic.AsyncAnthropic", return_value=_FakeAnthropic(catalogue)) as client,
        ):
            settings.return_value.anthropic_api_key = key
            settings.return_value.anthropic_model = model
            self.client = client
            return await _check_anthropic_model()

    async def test_reports_a_newer_opus_from_the_live_list(self):
        catalogue = [*CATALOGUE, _model("claude-opus-6", 2027, 2)]

        warnings = await self._run(key="sk-ant-abc", model="claude-opus-5-5", catalogue=catalogue)

        assert warnings == [ModelWarning("anthropic_newer_opus_available", "claude-opus-6")]

    async def test_without_an_api_key_the_api_is_not_called(self):
        warnings = await self._run(key="", model="claude-opus-5-5", catalogue=CATALOGUE)

        assert warnings == []
        self.client.assert_not_called()


def test_model_check_runs_weekly():
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["check-anthropic-model"]
    assert entry["task"] == "app.workers.model_check.check_anthropic_model"
    # One firing a week, at a fixed clock time so beat restarts cannot starve it.
    schedule = entry["schedule"]
    assert (len(schedule.day_of_week), len(schedule.hour), len(schedule.minute)) == (1, 1, 1)
