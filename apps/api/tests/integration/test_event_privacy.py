"""Calendar event addresses and attendees stay with the diary owner (finding S-13).

A Google Calendar event's location is often a street address (a school, a
home, a clinic) and its attendees are other people's names and emails. Only
the diary owner gets them; co-parents and viewers get each event's title,
time and description, plus the entry's town from its place line.
Decision: Andrew, 2026-11-12 ("owner-only"; descriptions stay visible).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import DiaryPermission, Entry, User
from tests.fixtures.factories import make_event

ADDRESS = "Kids Kuts LLC, 12811 8th Ave. W Suite B105, Everett, Washington"
ATTENDEES = [{"email": "grandma@example.com", "displayName": "Grandma"}]
DESCRIPTION = "Bring the coupon."


async def _shared_entry_with_event(client, db_session, role: str, prefix: str):
    """An owner's published entry with one calendar event, shared with *role*."""
    r = await client.post(
        "/v1/auth/register", json={"email": f"{prefix}-owner@example.com", "password": "Password1!"}
    )
    owner_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    diary = (
        await client.post(
            "/v1/diaries", json={"name": "D", "timezone": "America/Los_Angeles"}, headers=owner_auth
        )
    ).json()
    entry = (
        await client.post(
            f"/v1/diaries/{diary['id']}/entries",
            json={"entry_date": "2026-05-16"},
            headers=owner_auth,
        )
    ).json()
    stored = (
        await db_session.execute(select(Entry).where(Entry.id == uuid.UUID(entry["id"])))
    ).scalar_one()
    await make_event(
        db_session,
        entry=stored,
        payload={
            "summary": "Haircut",
            "location": ADDRESS,
            "description": DESCRIPTION,
            "start": {"dateTime": "2026-05-16T09:00:00-07:00"},
            "end": {"dateTime": "2026-05-16T09:30:00-07:00"},
            "attendees": ATTENDEES,
            "status": "confirmed",
        },
    )
    await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner_auth)

    r = await client.post(
        "/v1/auth/register",
        json={"email": f"{prefix}-{role}@example.com", "password": "Password1!"},
    )
    other_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other = (
        await db_session.execute(select(User).where(User.email == f"{prefix}-{role}@example.com"))
    ).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=other.id, role=role))
    await db_session.commit()
    return owner_auth, other_auth, diary, entry


async def _views(client, auth, diary, entry):
    """The entry as one read and as the diary's list returns it."""
    one = (await client.get(f"/v1/entries/{entry['id']}", headers=auth)).json()
    listed = (await client.get(f"/v1/diaries/{diary['id']}/entries", headers=auth)).json()
    return [one, next(e for e in listed if e["id"] == entry["id"])]


class TestEventAddressesStayWithTheOwner:
    async def test_owner_sees_address_and_attendees(self, client, db_session):
        owner_auth, _, diary, entry = await _shared_entry_with_event(
            client, db_session, "viewer", "s13-own"
        )

        for body in await _views(client, owner_auth, diary, entry):
            [event] = body["events"]
            assert event["location"] == ADDRESS
            assert event["attendees"] == ATTENDEES

    async def test_viewer_gets_no_address_or_attendees(self, client, db_session):
        _, viewer_auth, diary, entry = await _shared_entry_with_event(
            client, db_session, "viewer", "s13-view"
        )

        for body in await _views(client, viewer_auth, diary, entry):
            [event] = body["events"]
            assert event["location"] == ""
            assert event["attendees"] == []
            # Still there: what happened, when, and the description.
            assert event["summary"] == "Haircut"
            assert event["description"] == DESCRIPTION
            assert event["start"] == {"dateTime": "2026-05-16T09:00:00-07:00"}

    async def test_editor_gets_no_address_or_attendees_even_when_editing(
        self, client, db_session
    ):
        _, editor_auth, diary, entry = await _shared_entry_with_event(
            client, db_session, "editor", "s13-edit"
        )

        saved = (
            await client.patch(
                f"/v1/entries/{entry['id']}", json={"title": "Edited"}, headers=editor_auth
            )
        ).json()

        for body in [*await _views(client, editor_auth, diary, entry), saved]:
            [event] = body["events"]
            assert event["location"] == ""
            assert event["attendees"] == []


# The rule preview is owner-only altogether since S-14: test_rules_owner_only.py.
