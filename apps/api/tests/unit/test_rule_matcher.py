"""Unit tests for the condition tree matcher (match_event)."""

from __future__ import annotations

from app.workers.rules import match_event


def _payload(**kwargs) -> dict:
    return {
        "summary": kwargs.get("summary", ""),
        "description": kwargs.get("description", ""),
        "location": kwargs.get("location", ""),
        "attendees": kwargs.get("attendees", []),
    }


def test_title_contains_match():
    condition = {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_contains_no_match():
    condition = {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Piano lesson")) is False


def test_title_contains_case_sensitive_mismatch():
    condition = {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": True}
    assert match_event(condition, _payload(summary="soccer practice")) is False


def test_title_contains_case_sensitive_match():
    condition = {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": True}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_equals_match():
    condition = {"field": "title", "op": "equals", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer")) is True


def test_title_equals_no_match():
    condition = {"field": "title", "op": "equals", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is False


def test_title_not_contains_match():
    condition = {"field": "title", "op": "not_contains", "value": "zoom", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is True


def test_title_not_contains_no_match():
    condition = {"field": "title", "op": "not_contains", "value": "soccer", "case_sensitive": False}
    assert match_event(condition, _payload(summary="Soccer practice")) is False


def test_empty_value_always_false():
    condition = {"field": "title", "op": "contains", "value": "", "case_sensitive": False}
    assert match_event(condition, _payload(summary="anything")) is False


def test_missing_field_no_match():
    condition = {"field": "location", "op": "contains", "value": "park", "case_sensitive": False}
    assert match_event(condition, {}) is False


def test_missing_field_not_contains_match():
    condition = {"field": "location", "op": "not_contains", "value": "zoom", "case_sensitive": False}
    assert match_event(condition, {}) is True


def test_attendee_email_any_matches():
    condition = {"field": "attendee_email", "op": "contains", "value": "alice", "case_sensitive": False}
    attendees = [{"email": "alice@example.com"}, {"email": "bob@example.com"}]
    assert match_event(condition, _payload(attendees=attendees)) is True


def test_attendee_email_none_matches():
    condition = {"field": "attendee_email", "op": "contains", "value": "charlie", "case_sensitive": False}
    attendees = [{"email": "alice@example.com"}, {"email": "bob@example.com"}]
    assert match_event(condition, _payload(attendees=attendees)) is False


def test_attendee_email_empty_list_false():
    condition = {"field": "attendee_email", "op": "contains", "value": "alice", "case_sensitive": False}
    assert match_event(condition, _payload(attendees=[])) is False


def test_and_both_true():
    condition = {
        "op": "AND",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "location", "op": "contains", "value": "park", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is True


def test_and_one_false():
    condition = {
        "op": "AND",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "location", "op": "contains", "value": "zoom", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is False


def test_or_one_true():
    condition = {
        "op": "OR",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "title", "op": "contains", "value": "piano", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Piano lesson")) is True


def test_or_both_false():
    condition = {
        "op": "OR",
        "children": [
            {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
            {"field": "title", "op": "contains", "value": "piano", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Dentist appointment")) is False


def test_nested_and_of_or():
    condition = {
        "op": "AND",
        "children": [
            {
                "op": "OR",
                "children": [
                    {"field": "title", "op": "contains", "value": "soccer", "case_sensitive": False},
                    {"field": "title", "op": "contains", "value": "swimming", "case_sensitive": False},
                ],
            },
            {"field": "location", "op": "contains", "value": "park", "case_sensitive": False},
        ],
    }
    assert match_event(condition, _payload(summary="Soccer practice", location="City Park")) is True
    assert match_event(condition, _payload(summary="Soccer practice", location="Indoor gym")) is False
    assert match_event(condition, _payload(summary="Dentist", location="City Park")) is False


def test_empty_and_group_false():
    condition = {"op": "AND", "children": []}
    assert match_event(condition, _payload(summary="anything")) is False


def test_empty_or_group_false():
    condition = {"op": "OR", "children": []}
    assert match_event(condition, _payload(summary="anything")) is False
