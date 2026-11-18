"""Which event fields an edit changed (row 33): only those the drafting prompt reads."""

from app.workers.event_changes import event_edit_changes

BASE = {
    "summary": "Soccer",
    "location": "Field 3",
    "description": "Bring water",
    "attendees": [
        {
            "displayName": "Grandma",
            "email": "g@example.com",
            "organizer": False,
            "responseStatus": "needsAction",
        }
    ],
}


def _with(**kw):
    return {**BASE, **kw}


def test_no_change():
    assert event_edit_changes(BASE, dict(BASE)) == []


def test_each_field_is_named():
    assert event_edit_changes(BASE, _with(summary="Soccer final")) == ["summary"]
    assert event_edit_changes(BASE, _with(location="Field 4")) == ["location"]
    assert event_edit_changes(BASE, _with(description="")) == ["description"]


def test_several_fields_in_fixed_order():
    assert event_edit_changes(BASE, _with(description="x", summary="y")) == [
        "summary",
        "description",
    ]


def test_guest_added_or_renamed_is_an_edit():
    more = BASE["attendees"] + [{"displayName": "Sam", "email": "s@example.com"}]
    assert event_edit_changes(BASE, _with(attendees=more)) == ["guests"]
    renamed = [{**BASE["attendees"][0], "displayName": "Nana"}]
    assert event_edit_changes(BASE, _with(attendees=renamed)) == ["guests"]


def test_rsvp_change_is_not_an_edit():
    rsvp = [{**BASE["attendees"][0], "responseStatus": "accepted", "organizer": True}]
    assert event_edit_changes(BASE, _with(attendees=rsvp)) == []


def test_guest_order_is_not_an_edit():
    two = [{"displayName": "A", "email": "a@x"}, {"displayName": "B", "email": "b@x"}]
    assert event_edit_changes(_with(attendees=two), _with(attendees=list(reversed(two)))) == []


def test_missing_keys_equal_empty_values():
    assert (
        event_edit_changes(
            {"summary": "Soccer"},
            {"summary": "Soccer", "location": "", "description": "", "attendees": []},
        )
        == []
    )


def test_none_equals_empty():
    assert event_edit_changes(_with(description=None), _with(description="")) == []
    assert event_edit_changes(_with(attendees=None), _with(attendees=[])) == []


def test_no_stored_payload():
    assert event_edit_changes(None, BASE) == []
