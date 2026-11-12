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


# ---------------------------------------------------------------------------
# Regeneration-mode validator tests (tests 6-9 from spec)
# ---------------------------------------------------------------------------


class TestValidateCitationModes:
    def test_polish_mode_skips_validator_even_with_events(self):
        """Mode 'polish' always short-circuits — even if events are passed in."""
        events = [_event({"summary": "Soccer"})]
        parsed = {"title": "T", "body_markdown": "B", "facts_used": [99]}

        ok, err, flagged = validate_citation(parsed, events=events, mode="polish")

        assert ok is True

    def test_hybrid_empty_facts_used_accepted(self):
        """Mode 'hybrid': empty facts_used + empty title_facts_used is valid."""
        events = [_event({"summary": "Soccer"})]
        parsed = {
            "title": "A day",
            "title_facts_used": [],
            "body_markdown": "We played soccer.",
            "facts_used": [],
        }

        ok, err, flagged = validate_citation(parsed, events=events, mode="hybrid")

        assert ok is True

    def test_hybrid_out_of_range_facts_used_rejected(self):
        """Mode 'hybrid': index 5 when only 1 event → invalid."""
        events = [_event({"summary": "Soccer"})]
        parsed = {
            "title": "A day",
            "title_facts_used": [],
            "body_markdown": "We played.",
            "facts_used": [5],
        }

        ok, err, flagged = validate_citation(parsed, events=events, mode="hybrid")

        assert ok is False
        assert "invalid event index" in err

    def test_polish_mode_flags_nothing_when_every_word_is_the_parents(self):
        parsed = {"title": "Park day", "body_markdown": "Sam had a lovely Park day."}

        ok, err, flagged = validate_citation(
            parsed, events=[], mode="polish", body_seed="Park day\nSam went to the park"
        )

        assert (ok, err, flagged) == (True, "", [])


# ---------------------------------------------------------------------------
# Word check for the blend and polish modes (follow-up 25.3)
# ---------------------------------------------------------------------------


class TestWordCheckWithParentsText:
    def test_hybrid_does_not_flag_a_word_the_parent_wrote(self):
        events = [_event({"summary": "park visit"})]
        parsed = {
            "title": "",
            "title_facts_used": [],
            "body_markdown": "We saw Grandma at the park.",
            "facts_used": [1],
        }

        _, _, flagged = validate_citation(
            parsed, events=events, mode="hybrid", body_seed="We saw Grandma"
        )

        assert flagged == []

    def test_hybrid_flags_a_word_in_neither_the_events_nor_the_parents_text(self):
        events = [_event({"summary": "park visit"})]
        parsed = {
            "title": "",
            "title_facts_used": [],
            "body_markdown": "We saw Grandma, then went to Disneyland.",
            "facts_used": [1],
        }

        _, _, flagged = validate_citation(
            parsed, events=events, mode="hybrid", body_seed="We saw Grandma"
        )

        assert flagged == ["Disneyland"]

    def test_hybrid_does_not_flag_a_word_from_a_cited_event(self):
        events = [_event({"summary": "Lunch with Priya"})]
        parsed = {
            "title": "",
            "title_facts_used": [],
            "body_markdown": "We had lunch with Priya.",
            "facts_used": [1],
        }

        _, _, flagged = validate_citation(
            parsed, events=events, mode="hybrid", body_seed="Lunch was nice"
        )

        assert flagged == []

    def test_hybrid_flags_a_word_found_only_in_an_uncited_event(self):
        events = [_event({"summary": "park visit"}), _event({"summary": "Dinner with Priya"})]
        parsed = {
            "title": "",
            "title_facts_used": [],
            "body_markdown": "We went to the park with Priya.",
            "facts_used": [1],
        }

        _, _, flagged = validate_citation(
            parsed, events=events, mode="hybrid", body_seed="A good day"
        )

        assert flagged == ["Priya"]

    def test_hybrid_checks_the_title_too(self):
        events = [_event({"summary": "park visit"})]
        parsed = {
            "title": "Lunch at Nandos",
            "title_facts_used": [],
            "body_markdown": "A day at the park.",
            "facts_used": [1],
        }

        _, _, flagged = validate_citation(
            parsed, events=events, mode="hybrid", body_seed="A day at the park"
        )

        assert flagged == ["Lunch", "Nandos"]

    def test_polish_flags_a_word_not_in_the_parents_text(self):
        parsed = {"title": "", "body_markdown": "Park day with Sam at the Zoo."}

        _, _, flagged = validate_citation(
            parsed, events=[], mode="polish", body_seed="Park day with Sam"
        )

        assert flagged == ["Zoo"]

    def test_polish_does_not_flag_a_word_from_the_weather(self):
        from types import SimpleNamespace

        parsed = {"title": "", "body_markdown": "Park day with Sam. It was Sunny."}
        enrichments = [SimpleNamespace(payload={"condition": "Sunny"})]

        _, _, flagged = validate_citation(
            parsed,
            events=[],
            mode="polish",
            body_seed="Park day with Sam",
            enrichments=enrichments,
        )

        assert flagged == []


# ---------------------------------------------------------------------------
# Enrichment payload tests (item 16, task 7)
# ---------------------------------------------------------------------------


class TestValidateCitationEnrichments:
    def test_validate_citation_allows_words_present_in_enrichment_payload(self):
        from types import SimpleNamespace

        events = [SimpleNamespace(payload={"summary": "Park visit"})]
        enrichments = [SimpleNamespace(payload={
            "date": "2024-01-15", "condition": "Sunny", "location": "Brooklyn",
        })]
        output = {
            "body_markdown": "It was Sunny in Brooklyn at the park.",
            "title": "Park morning",
            "facts_used": [1],
            "title_facts_used": [1],
        }
        ok, msg, flagged = validate_citation(output, events, mode="events", enrichments=enrichments)
        assert ok is True, msg
        assert flagged == []

    def test_validate_citation_still_flags_unrelated_capitalized_tokens(self):
        from types import SimpleNamespace

        events = [SimpleNamespace(payload={"summary": "Park visit"})]
        enrichments = [SimpleNamespace(payload={"condition": "clear sky"})]
        output = {
            "body_markdown": "We saw Aunt Mildred at the park.",
            "title": "T",
            "facts_used": [1],
            "title_facts_used": [1],
        }
        ok, msg, flagged = validate_citation(output, events, mode="events", enrichments=enrichments)
        assert "Mildred" in flagged or "Aunt" in flagged

