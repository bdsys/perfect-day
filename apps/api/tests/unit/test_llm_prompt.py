"""Unit tests: LLM prompt builder."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.workers.llm import _format_duration, _format_event_line, build_prompt


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
    ev.occurred_at = datetime(2026, 5, 10, 16, 0, tzinfo=UTC)
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


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_minutes_only(self):
        assert _format_duration(30) == "30m"

    def test_exactly_one_hour(self):
        assert _format_duration(60) == "1h"

    def test_hours_and_minutes(self):
        assert _format_duration(90) == "1h 30m"

    def test_multi_hour(self):
        assert _format_duration(150) == "2h 30m"

    def test_zero_minutes(self):
        assert _format_duration(0) == "0m"


# ---------------------------------------------------------------------------
# _format_event_line
# ---------------------------------------------------------------------------


def _make_event(
    *,
    source: str = "google_calendar",
    occurred_at: datetime | None = None,
    payload: dict | None = None,
) -> MagicMock:
    ev = MagicMock()
    ev.source = source
    ev.occurred_at = occurred_at or datetime(2026, 5, 19, 16, 0, tzinfo=UTC)
    ev.payload = payload or {}
    return ev


class TestFormatEventLine:
    def test_basic_summary_and_start_time_falls_back_to_occurred_at(self):
        ev = _make_event(payload={"summary": "Standup"})
        line = _format_event_line(1, ev)
        assert '<event index="1">' in line
        assert "[google_calendar]" in line
        assert "Standup" in line
        assert "2026-05-19" in line  # occurred_at date
        assert "</event>" in line

    def test_event_with_dateTime_start_only_uses_start_iso(self):
        ev = _make_event(payload={
            "summary": "Lunch",
            "start": {"dateTime": "2026-05-19T12:00:00-07:00"},
        })
        line = _format_event_line(1, ev)
        assert "2026-05-19T12:00:00-07:00" in line
        assert "Lunch" in line
        # No duration shown since no end
        assert "(" not in line

    def test_event_with_end_time_shows_range_and_duration(self):
        ev = _make_event(payload={
            "summary": "Standup",
            "start": {"dateTime": "2026-05-19T09:00:00-07:00"},
            "end": {"dateTime": "2026-05-19T09:30:00-07:00"},
        })
        line = _format_event_line(1, ev)
        assert "2026-05-19T09:00:00-07:00" in line
        assert "09:30:00-07:00" in line
        assert "(30m)" in line

    def test_long_duration_renders_hours_and_minutes(self):
        ev = _make_event(payload={
            "summary": "Workshop",
            "start": {"dateTime": "2026-05-19T09:00:00-07:00"},
            "end": {"dateTime": "2026-05-19T10:30:00-07:00"},
        })
        line = _format_event_line(1, ev)
        assert "(1h 30m)" in line

    def test_attendees_display_names_joined(self):
        ev = _make_event(payload={
            "summary": "Standup",
            "start": {"dateTime": "2026-05-19T09:00:00-07:00"},
            "end": {"dateTime": "2026-05-19T09:30:00-07:00"},
            "attendees": [
                {"displayName": "Sara", "email": "sara@example.com"},
                {"displayName": "Andrew", "email": "andrew@example.com"},
            ],
        })
        line = _format_event_line(1, ev)
        assert 'attendees: "Sara, Andrew"' in line

    def test_attendee_falls_back_to_email_when_no_display_name(self):
        ev = _make_event(payload={
            "summary": "1:1",
            "attendees": [
                {"displayName": "", "email": "boss@example.com"},
            ],
        })
        line = _format_event_line(1, ev)
        assert "boss@example.com" in line

    def test_attendee_skipped_when_both_name_and_email_empty(self):
        ev = _make_event(payload={
            "summary": "Mystery",
            "attendees": [{"displayName": "", "email": ""}],
        })
        line = _format_event_line(1, ev)
        assert "attendees" not in line

    def test_empty_attendees_list_omits_attendees(self):
        ev = _make_event(payload={"summary": "Solo", "attendees": []})
        line = _format_event_line(1, ev)
        assert "attendees" not in line

    def test_description_included_when_present(self):
        ev = _make_event(payload={
            "summary": "Standup",
            "description": "Daily eng sync",
        })
        line = _format_event_line(1, ev)
        assert 'description: "Daily eng sync"' in line

    def test_description_omitted_when_empty(self):
        ev = _make_event(payload={"summary": "Standup", "description": ""})
        line = _format_event_line(1, ev)
        assert "description" not in line

    def test_description_omitted_when_absent(self):
        ev = _make_event(payload={"summary": "Standup"})
        line = _format_event_line(1, ev)
        assert "description" not in line

    def test_all_day_event_shows_all_day(self):
        ev = _make_event(payload={
            "summary": "Holiday",
            "start": {"date": "2026-05-19"},
            "end": {"date": "2026-05-20"},
        })
        line = _format_event_line(1, ev)
        assert "all day" in line
        assert "Holiday" in line
        # No duration in parens for all-day
        assert "(" not in line

    def test_full_event_with_all_fields(self):
        ev = _make_event(payload={
            "summary": "Standup",
            "start": {"dateTime": "2026-05-19T09:00:00-07:00"},
            "end": {"dateTime": "2026-05-19T09:30:00-07:00"},
            "location": "Zoom",
            "attendees": [
                {"displayName": "Sara", "email": "sara@example.com"},
                {"displayName": "Andrew", "email": "andrew@example.com"},
            ],
            "description": "Daily eng sync",
        })
        line = _format_event_line(1, ev)
        assert "[google_calendar]" in line
        assert "(30m)" in line
        assert '"Standup"' in line
        assert 'location: "Zoom"' in line
        assert 'attendees: "Sara, Andrew"' in line
        assert 'description: "Daily eng sync"' in line

    def test_ordering_location_before_attendees_before_description(self):
        ev = _make_event(payload={
            "summary": "Meeting",
            "location": "Room 1",
            "attendees": [{"displayName": "Bob", "email": "bob@example.com"}],
            "description": "Catch-up",
        })
        line = _format_event_line(1, ev)
        loc_idx = line.index("location")
        att_idx = line.index("attendees")
        desc_idx = line.index("description")
        assert loc_idx < att_idx < desc_idx


# ---------------------------------------------------------------------------
# Regeneration-mode prompt tests (tests 1-5 from spec)
# ---------------------------------------------------------------------------


def _entry_with_title(title: str | None = None) -> MagicMock:
    e = MagicMock()
    e.entry_date = "2026-05-10"
    e.entry_end_date = None
    e.title = title
    return e


class TestBuildPromptModes:
    def test_polish_mode_includes_draft_body_and_no_events_section(self):
        """Mode 'polish': per-entry message contains <draft_body> + body_seed, no EVENTS."""
        diary = _diary()
        entry = _entry_with_title(title=None)
        body_seed = "We went to the park today."

        _, per_entry = build_prompt(diary, entry, [], [], mode="polish", body_seed=body_seed)

        assert "<draft_body>" in per_entry
        assert body_seed in per_entry
        assert "EVENTS:" not in per_entry

    def test_hybrid_mode_includes_both_draft_body_and_events(self):
        """Mode 'hybrid': per-entry message contains <draft_body> AND <event index="1">."""
        diary = _diary()
        entry = _entry_with_title(title=None)
        body_seed = "Alice had soccer."
        events = [_event("Soccer practice")]

        _, per_entry = build_prompt(diary, entry, events, [], mode="hybrid", body_seed=body_seed)

        assert "<draft_body>" in per_entry
        assert body_seed in per_entry
        assert '<event index="1">' in per_entry

    def test_polish_mode_diary_context_byte_identical_to_events_mode(self):
        """Diary context (first return value) is byte-for-byte identical across modes
        to allow Anthropic prompt cache reuse."""
        diary = _diary()
        entry_events = _entry()
        entry_polish = _entry_with_title(title=None)

        ctx_events, _ = build_prompt(diary, entry_events, [_event("Soccer")], [], mode="events")
        ctx_polish, _ = build_prompt(diary, entry_polish, [], [], mode="polish", body_seed="X")

        assert ctx_events == ctx_polish

    def test_polish_includes_current_title_when_non_empty(self):
        """Mode 'polish' with non-empty entry title: CURRENT_TITLE appears in per-entry message."""
        diary = _diary()
        entry = _entry_with_title(title="Summer Fun")

        _, per_entry = build_prompt(
            diary, entry, [], [], mode="polish", body_seed="Some body text."
        )

        assert "CURRENT_TITLE:" in per_entry
        assert "Summer Fun" in per_entry

    def test_polish_omits_current_title_when_empty(self):
        """Mode 'polish' with empty/None title: CURRENT_TITLE does NOT appear in per-entry message."""
        diary = _diary()
        entry_none = _entry_with_title(title=None)

        _, per_entry_none = build_prompt(
            diary, entry_none, [], [], mode="polish", body_seed="Some body text."
        )
        assert "CURRENT_TITLE:" not in per_entry_none

        entry_empty = _entry_with_title(title="")
        _, per_entry_empty = build_prompt(
            diary, entry_empty, [], [], mode="polish", body_seed="Some body text."
        )
        assert "CURRENT_TITLE:" not in per_entry_empty

