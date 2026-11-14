"""Unit tests: draft text rules (TR-7 verify words, TR-8 attendee emails, TR-9 plain text)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.workers.llm import (
    SYSTEM_PROMPT_EVENTS,
    SYSTEM_PROMPT_HYBRID,
    SYSTEM_PROMPT_POLISH,
    _build_fallback_body,
    _format_event_line,
    _strip_markdown,
    validate_citation,
)


def _event(payload: dict) -> MagicMock:
    m = MagicMock()
    m.payload = payload
    m.source = "google_calendar"
    m.occurred_at = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    return m


def _flagged(
    body: str, source: dict | None = None, known_words: str = ""
) -> list[str]:
    events = [_event(source or {"summary": "Park"})]
    out = {"title": "", "title_facts_used": [], "body_markdown": body, "facts_used": [1]}
    ok, _, flagged = validate_citation(out, events, known_words=known_words)
    assert ok
    return flagged


def test_subject_name_not_flagged():
    assert _flagged("Mia played soccer.", {"summary": "soccer"}, known_words="Mia") == []


def test_sentence_starter_not_flagged():
    assert _flagged("After lunch they swam.", {"summary": "lunch swim"}) == []


def test_invented_name_at_sentence_start_flagged():
    assert _flagged("Grandma visited.", {"summary": "visit"}) == ["Grandma"]


def test_case_insensitive_match():
    assert _flagged("Soccer Park", {"summary": "soccer park"}) == []


def test_known_words_apply_in_polish_mode():
    out = {"title": "", "body_markdown": "Mia swam."}
    _, _, flagged = validate_citation(out, [], mode="polish", body_seed="swam", known_words="Mia")
    assert flagged == []


def test_known_words_apply_on_invalid_index_path():
    out = {"title": "", "body_markdown": "x", "facts_used": [9]}
    ok, _, flagged = validate_citation(out, [_event({})], known_words="Mia")
    assert ok is False and flagged == []


def test_nameless_attendees_never_send_email():
    ev = _event({"summary": "Play", "attendees": [
        {"displayName": "Ana"}, {"email": "x@y.com"}, {"email": "z@y.com"}]})
    line = _format_event_line(1, ev)
    assert 'attendees: "Ana and 2 others"' in line
    assert "@" not in line


def test_one_nameless_attendee_alongside_name():
    ev = _event({"summary": "Play", "attendees": [
        {"displayName": "Ana"}, {"email": "x@y.com"}]})
    assert 'attendees: "Ana and 1 other"' in _format_event_line(1, ev)


def test_all_nameless_attendees():
    ev = _event({"summary": "Play", "attendees": [{"email": "x@y.com"}]})
    line = _format_event_line(1, ev)
    assert 'attendees: "1 person"' in line
    assert "@" not in line


def test_all_nameless_attendees_plural():
    ev = _event({"summary": "Play", "attendees": [{"email": "x@y.com"}, {"email": "z@y.com"}]})
    assert 'attendees: "2 people"' in _format_event_line(1, ev)


def test_fallback_body_has_no_markdown():
    ev = _event({
        "summary": "Standup", "location": "Zoom",
        "start": {"dateTime": "2026-05-01T09:00:00+00:00"},
        "end": {"dateTime": "2026-05-01T09:30:00+00:00"},
    })
    _, body = _build_fallback_body([ev], datetime(2026, 5, 1).date())
    assert "**" not in body
    assert body.strip() == "- 09:00–09:30  Standup — Zoom"


def test_prompt_forbids_markdown():
    for p in (SYSTEM_PROMPT_EVENTS, SYSTEM_PROMPT_HYBRID, SYSTEM_PROMPT_POLISH):
        assert "Write plain sentences. Do not use Markdown (no **, #, or _ formatting)." in p


def test_short_invented_name_not_hidden_by_substring():
    assert _flagged("We met Tom at the park.", {"summary": "tomorrow"}) == ["Tom"]


# --- TR-9: Markdown markers are stripped from model text before it is stored ---


def test_strip_markdown_removes_paired_emphasis():
    assert _strip_markdown("**Mia** swam at __Lake Pleasant__.") == "Mia swam at Lake Pleasant."


def test_strip_markdown_removes_leading_heading_marker():
    assert _strip_markdown("# Day at the beach\nWe swam.") == "Day at the beach\nWe swam."
    assert _strip_markdown("We swam.\n###### Later\nWe ate.") == "We swam.\nLater\nWe ate."


def test_strip_markdown_leaves_single_markers_and_bullets():
    assert _strip_markdown("snake_case and 2*3") == "snake_case and 2*3"
    assert _strip_markdown("- swim\n- lunch") == "- swim\n- lunch"
    assert _strip_markdown("Mia was #1 today.") == "Mia was #1 today."


# --- Verify words: the haystack is the event text the model was shown ---


def test_name_after_newline_in_description_is_supported():
    src = {"summary": "Soccer", "description": "Carpool:\nSarah drives"}
    assert _flagged("Sarah drove everyone to soccer.", src) == []


def test_attendee_email_does_not_support_a_name():
    src = {"summary": "Visit", "attendees": [{"email": "grandma.tom@x.com"}]}
    assert _flagged("Grandma came.", src) == ["Grandma"]


def test_json_key_does_not_support_a_word():
    # "location" is a key in the payload but not in the event line the model saw.
    src = {"summary": "Park", "location": ""}
    assert _flagged("Location was great.", src) == ["Location"]
    assert _flagged("Summary was great.", {"summary": "Park"}) == ["Summary"]


def test_invented_count_at_sentence_start_flagged():
    assert _flagged("Three friends came.", {"summary": "Park"}) == ["Three"]
