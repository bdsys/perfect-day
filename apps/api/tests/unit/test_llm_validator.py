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
        ok, err = validate_citation(output, events)
        assert ok is True
        assert err == ""

    def test_out_of_range_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [2], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err = validate_citation(output, events)
        assert ok is False
        assert "invalid event index" in err

    def test_zero_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [0], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err = validate_citation(output, events)
        assert ok is False

    def test_negative_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [-1], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err = validate_citation(output, events)
        assert ok is False

    def test_non_int_index_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": ["1"], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, err = validate_citation(output, events)
        assert ok is False

    def test_empty_facts_accepted(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [], "title_facts_used": [], "body_markdown": "", "title": ""}
        ok, _ = validate_citation(output, events)
        assert ok is True

    def test_missing_facts_key_treated_as_empty(self):
        events = [_event({"summary": "Event A"})]
        output = {"body_markdown": "content", "title": "t"}
        ok, _ = validate_citation(output, events)
        assert ok is True

    def test_title_facts_out_of_range_rejected(self):
        events = [_event({"summary": "Event A"})]
        output = {"facts_used": [], "title_facts_used": [5], "body_markdown": "", "title": ""}
        ok, err = validate_citation(output, events)
        assert ok is False
        assert "invalid event index" in err
