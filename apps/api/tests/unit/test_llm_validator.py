"""Unit tests: LLM citation validator."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.workers.llm import validate_citation


def _event(payload: dict) -> MagicMock:
    m = MagicMock()
    m.payload = payload
    return m


class TestValidateCitation:
    def test_valid_indices_accepted(self):
        events = [_event({"summary": "School pickup"}), _event({"summary": "Football practice"})]
        output = {
            "title": "After School",
            "title_facts_used": [1],
            "body_markdown": "School pickup then Football practice.",
            "facts_used": [1, 2],
        }
        ok, err, _ = validate_citation(output, events)
        assert ok is True
        assert err == ""

    def test_out_of_range_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [2], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err, _ = validate_citation(output, events)
        assert ok is False
        assert "invalid event index" in err

    def test_zero_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [0], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err, _ = validate_citation(output, events)
        assert ok is False

    def test_negative_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [-1], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err, _ = validate_citation(output, events)
        assert ok is False

    def test_non_int_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": ["1"], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err, _ = validate_citation(output, events)
        assert ok is False

    def test_empty_facts_accepted(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, _, _ = validate_citation(output, events)
        assert ok is True

    def test_missing_facts_key_treated_as_empty(self):
        events = [_event({"summary": "Event A"})]
        output = {"body_markdown": "content", "title": "t"}
        ok, _, _ = validate_citation(output, events)
        assert ok is True

    def test_title_facts_out_of_range_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [], "title_facts_used": [5], "body_markdown": "", "title": ""}
        ok, err, _ = validate_citation(output, events)
        assert ok is False
        assert "invalid event index" in err

    def test_flagged_tokens_returned_for_unknown_names(self):
        events = [_event({"summary": "soccer practice"})]
        output = {
            "facts_used": [1],
            "title_facts_used": [],
            "body_markdown": "Sarah went to soccer practice.",
            "title": "",
        }
        ok, _, flagged = validate_citation(output, events)
        assert ok is True
        assert "Sarah" in flagged

    def test_calendar_words_not_flagged(self):
        events = [_event({"summary": "park visit"})]
        output = {
            "facts_used": [1],
            "title_facts_used": [],
            "body_markdown": "On Monday in January we visited the park.",
            "title": "",
        }
        ok, _, flagged = validate_citation(output, events)
        assert ok is True
        assert "Monday" not in flagged
        assert "January" not in flagged

    def test_name_in_event_payload_not_flagged(self):
        events = [_event({"summary": "Meet with Sarah"})]
        output = {
            "facts_used": [1],
            "title_facts_used": [],
            "body_markdown": "We met with Sarah.",
            "title": "",
        }
        ok, _, flagged = validate_citation(output, events)
        assert ok is True
        assert "Sarah" not in flagged

