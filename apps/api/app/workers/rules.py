"""Rule engine: condition tree matcher."""

from __future__ import annotations

import structlog

log = structlog.get_logger()


def match_event(condition: dict, payload: dict) -> bool:
    """Recursively evaluate a condition tree against an event payload.

    Empty groups → False. Empty leaf value → False.
    """
    op = condition.get("op")

    if op in ("AND", "OR"):
        children = condition.get("children") or []
        if not children:
            return False
        if op == "AND":
            return all(match_event(child, payload) for child in children)
        return any(match_event(child, payload) for child in children)

    # Leaf node
    field = condition.get("field", "")
    leaf_op = condition.get("op", "contains")
    value: str = condition.get("value", "")
    case_sensitive: bool = condition.get("case_sensitive", False)

    if not value:
        return False

    if field == "attendee_email":
        attendees = payload.get("attendees") or []
        emails = [str(a.get("email", "")) for a in attendees if isinstance(a, dict)]
        return _match_any(emails, leaf_op, value, case_sensitive)

    field_map = {
        "title": "summary",
        "description": "description",
        "location": "location",
    }
    payload_key = field_map.get(field, field)
    field_value = str(payload.get(payload_key) or "")
    return _match_string(field_value, leaf_op, value, case_sensitive)


def _match_string(field_value: str, op: str, value: str, case_sensitive: bool) -> bool:
    if not case_sensitive:
        field_value = field_value.lower()
        value = value.lower()
    if op == "contains":
        return value in field_value
    if op == "equals":
        return field_value == value
    if op == "not_contains":
        return value not in field_value
    return False


def _match_any(values: list[str], op: str, value: str, case_sensitive: bool) -> bool:
    if op == "not_contains":
        return all(_match_string(v, op, value, case_sensitive) for v in values)
    return any(_match_string(v, op, value, case_sensitive) for v in values)
