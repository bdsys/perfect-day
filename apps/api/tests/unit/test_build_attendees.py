"""Unit tests: _build_attendees attendee extraction edge cases."""

from __future__ import annotations

from app.workers.tasks import _build_attendees


class TestBuildAttendeesAbsent:
    """Key absent entirely → empty list."""

    def test_none_input_returns_empty(self):
        # Simulates event_data.get("attendees") when the key is missing
        assert _build_attendees(None) == []

    def test_empty_list_returns_empty(self):
        assert _build_attendees([]) == []


class TestBuildAttendeesNullValue:
    """Google Calendar can return ``"attendees": null`` — the crash case."""

    def test_null_value_returns_empty(self):
        # event_data.get("attendees") returns None when key exists with null value
        result = _build_attendees(None)
        assert result == []

    def test_falsy_values_coerced_to_empty(self):
        # Extra robustness: any other falsy raw value
        for falsy in (None, [], 0, False, ""):
            assert _build_attendees(falsy) == []


class TestBuildAttendeesNonDictElement:
    """Non-dict element in the list → skip it, process valid ones."""

    def test_all_non_dict_returns_empty(self):
        result = _build_attendees(["string", 42, None, True])
        assert result == []

    def test_non_dict_mixed_with_valid(self):
        raw = [
            "not-a-dict",
            {"email": "alice@example.com", "displayName": "Alice"},
            None,
            {"email": "bob@example.com"},
        ]
        result = _build_attendees(raw)
        emails = [a["email"] for a in result]
        assert emails == ["alice@example.com", "bob@example.com"]

    def test_single_non_dict_returns_empty(self):
        result = _build_attendees([42])
        assert result == []


class TestBuildAttendeesHappyPath:
    """Valid attendees are correctly normalised and stored."""

    def test_display_name_and_email_stored(self):
        raw = [{"displayName": "Alice Smith", "email": "alice@example.com"}]
        result = _build_attendees(raw)
        assert len(result) == 1
        assert result[0]["displayName"] == "Alice Smith"
        assert result[0]["email"] == "alice@example.com"

    def test_organizer_flag_preserved(self):
        raw = [{"email": "org@example.com", "organizer": True}]
        result = _build_attendees(raw)
        assert result[0]["organizer"] is True

    def test_organizer_defaults_false(self):
        raw = [{"email": "attendee@example.com"}]
        result = _build_attendees(raw)
        assert result[0]["organizer"] is False

    def test_response_status_preserved(self):
        raw = [{"email": "a@example.com", "responseStatus": "accepted"}]
        result = _build_attendees(raw)
        assert result[0]["responseStatus"] == "accepted"

    def test_response_status_defaults_empty_string(self):
        raw = [{"email": "a@example.com"}]
        result = _build_attendees(raw)
        assert result[0]["responseStatus"] == ""

    def test_attendee_with_no_name_or_email_excluded(self):
        # Entry has neither displayName nor email — should be skipped
        raw = [{"organizer": True, "responseStatus": "accepted"}]
        result = _build_attendees(raw)
        assert result == []

    def test_multiple_attendees(self):
        raw = [
            {"displayName": "Alice", "email": "alice@example.com", "responseStatus": "accepted"},
            {"displayName": "Bob", "email": "bob@example.com", "responseStatus": "declined"},
        ]
        result = _build_attendees(raw)
        assert len(result) == 2
        assert result[0]["displayName"] == "Alice"
        assert result[1]["displayName"] == "Bob"

    def test_null_display_name_field_coerced_to_empty(self):
        # Google sometimes returns "displayName": null
        raw = [{"displayName": None, "email": "a@example.com"}]
        result = _build_attendees(raw)
        assert result[0]["displayName"] == ""
        assert result[0]["email"] == "a@example.com"
