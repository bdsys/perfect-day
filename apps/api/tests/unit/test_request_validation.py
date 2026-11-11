"""Unit tests: request schemas reject values that would break workers or hit DB constraints."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.routers.v1.diaries import DiaryCreate, DiaryPatch
from app.routers.v1.entries import EntryCreate, EntryPatch


def test_diary_create_accepts_valid_payload():
    d = DiaryCreate(name="Maya", timezone="America/Chicago", subject_relation="child")
    assert d.scan_interval_minutes == 60
    assert d.tone_hint == "warm, narrative"


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": "Mars/Olympus_Mons"},
        {"timezone": ""},
        {"name": ""},
        {"subject_relation": "pet"},
        {"voice_override": "fourth"},
        {"scan_interval_minutes": 0},
        {"scan_interval_minutes": -5},
        {"tone_hint": ""},
    ],
)
def test_diary_create_rejects_bad_values(overrides: dict):
    payload = {"name": "Maya", "timezone": "America/Chicago", **overrides}
    with pytest.raises(ValidationError):
        DiaryCreate(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Not/AZone"},
        {"slug": "Has Spaces"},
        {"slug": "../etc"},
        {"scan_interval_minutes": 1},
        {"subject_relation": "pet"},
    ],
)
def test_diary_patch_rejects_bad_values(payload: dict):
    with pytest.raises(ValidationError):
        DiaryPatch(**payload)


def test_diary_patch_accepts_partial_and_null_location():
    p = DiaryPatch(slug="maya-2026", lat=None, lon=None, timezone="Europe/London")
    assert p.model_dump(exclude_unset=True) == {
        "slug": "maya-2026",
        "lat": None,
        "lon": None,
        "timezone": "Europe/London",
    }


def test_entry_create_rejects_end_before_start():
    with pytest.raises(ValidationError):
        EntryCreate(entry_date=date(2026, 6, 10), entry_end_date=date(2026, 6, 9))
    ok = EntryCreate(entry_date=date(2026, 6, 10), entry_end_date=date(2026, 6, 10))
    assert ok.entry_end_date == date(2026, 6, 10)


def test_entry_patch_rejects_null_entry_date_but_allows_clearing_end_date():
    with pytest.raises(ValidationError):
        EntryPatch(entry_date=None)
    cleared = EntryPatch(entry_end_date=None, body_markdown=None)
    assert cleared.model_dump(exclude_unset=True) == {"entry_end_date": None, "body_markdown": None}


def test_diary_patch_can_clear_nullable_fields_only():
    from app.routers.v1.diaries import _diary_updates

    patch = DiaryPatch(voice_override=None, subject_name=None, lat=None, name=None, tone_hint=None)
    # voice/subject/location are nullable columns; name and tone_hint are NOT NULL.
    assert _diary_updates(patch) == {"voice_override": None, "subject_name": None, "lat": None}

    patch = DiaryPatch(name="New name", scan_enabled=False)
    assert _diary_updates(patch) == {"name": "New name", "scan_enabled": False}


def test_entry_cursor_predicate_orders_by_date_then_created_then_id():
    """The pagination predicate must use the same three keys as the timeline ordering."""
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from app.routers.v1.entries import ENTRY_TIMELINE_ORDER, _after_cursor

    predicate = _after_cursor(
        date(2026, 6, 10), datetime(2026, 6, 10, 12, tzinfo=UTC), uuid.uuid4()
    )
    sql = str(predicate.compile(dialect=postgresql.dialect()))
    assert sql.count("entries.entry_date <") == 1
    assert sql.count("entries.entry_date =") == 2
    assert sql.count("entries.created_at <") == 1
    assert sql.count("entries.id <") == 1
    assert [str(c.compile(dialect=postgresql.dialect())) for c in ENTRY_TIMELINE_ORDER] == [
        "entries.entry_date DESC",
        "entries.created_at DESC",
        "entries.id DESC",
    ]
