"""Unit tests: LLM prompt builder."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.workers.llm import build_prompt


def _diary(
    *,
    subject_name: str = "Alice",
    subject_relation: str = "child",
    voice_override: str | None = None,
    tone_hint: str = "warm",
    timezone: str = "America/Chicago",
) -> MagicMock:
    d = MagicMock()
    d.subject_name = subject_name
    d.subject_relation = subject_relation
    d.voice_override = voice_override
    d.tone_hint = tone_hint
    d.timezone = timezone
    return d


def _entry(
    *,
    entry_date="2026-05-10",
    entry_end_date=None,
) -> MagicMock:
    e = MagicMock()
    e.entry_date = entry_date
    e.entry_end_date = entry_end_date
    return e


def _event(summary: str, index_hint: int = 1, location: str = "") -> MagicMock:
    ev = MagicMock()
    ev.source = "google_calendar"
    ev.occurred_at = datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc)
    ev.payload = {"summary": summary, "location": location}
    return ev


class TestBuildPrompt:
    def test_diary_context_contains_subject_relation_and_voice(self):
        diary = _diary(subject_name="Alice", subject_relation="child")
        entry = _entry()
        ctx, _ = build_prompt(diary, entry, [_event("Soccer practice")], [])
        assert "child" in ctx
        assert "Alice" in ctx
        assert "you" in ctx  # child → second person pronoun

    def test_events_are_1_indexed_in_xml_tags(self):
        diary = _diary()
        entry = _entry()
        events = [_event("Soccer 4pm"), _event("Pizza dinner 6pm")]
        _, per_entry = build_prompt(diary, entry, events, [])
        assert '<event index="1">' in per_entry
        assert '<event index="2">' in per_entry
        assert '<event index="0">' not in per_entry

    def test_event_summaries_appear_in_entry_section(self):
        diary = _diary()
        entry = _entry()
        _, per_entry = build_prompt(diary, entry, [_event("Swimming lesson")], [])
        assert "Swimming lesson" in per_entry

    def test_date_range_rendered_when_end_date_present(self):
        diary = _diary()
        entry = _entry(entry_date="2026-05-10", entry_end_date="2026-05-12")
        _, per_entry = build_prompt(diary, entry, [_event("Camping trip")], [])
        assert "DATE_RANGE" in per_entry
        assert "2026-05-10" in per_entry
        assert "2026-05-12" in per_entry

    def test_single_date_rendered_when_no_end_date(self):
        diary = _diary()
        entry = _entry(entry_date="2026-05-10", entry_end_date=None)
        _, per_entry = build_prompt(diary, entry, [_event("School pickup")], [])
        assert "DATE: 2026-05-10" in per_entry
        assert "DATE_RANGE" not in per_entry

    def test_enrichments_section_present_when_enrichments_provided(self):
        diary = _diary()
        entry = _entry()
        enrichment = MagicMock()
        enrichment.kind = "weather"
        enrichment.payload = {"temp_c": 22, "condition": "sunny"}
        _, per_entry = build_prompt(diary, entry, [_event("Park visit")], [enrichment])
        assert "ENRICHMENTS" in per_entry
        assert "weather" in per_entry

    def test_no_enrichments_section_when_empty(self):
        diary = _diary()
        entry = _entry()
        _, per_entry = build_prompt(diary, entry, [_event("School pickup")], [])
        assert "ENRICHMENTS" not in per_entry

    def test_voice_override_respected(self):
        diary = _diary(voice_override="first_plural")
        entry = _entry()
        ctx, _ = build_prompt(diary, entry, [_event("Family dinner")], [])
        assert "we" in ctx.lower()

    def test_location_included_in_event_when_present(self):
        diary = _diary()
        entry = _entry()
        _, per_entry = build_prompt(diary, entry, [_event("Soccer", location="Main Field")], [])
        assert "Main Field" in per_entry

    def test_location_omitted_when_empty(self):
        diary = _diary()
        entry = _entry()
        ev = _event("Soccer")
        ev.payload = {"summary": "Soccer", "location": ""}
        _, per_entry = build_prompt(diary, entry, [ev], [])
        assert "location" not in per_entry
