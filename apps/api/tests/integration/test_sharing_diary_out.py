"""What diary responses tell members: who shared it, counts, deletion notice, decline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import Diary, User
from tests.integration.sharing_helpers import (
    accept,
    invite,
    make_shared_diary,
    register,
    set_tier,
)


async def _join(client, owner, did, email):
    created = await invite(client, owner, did, email)
    headers = await register(client, email)
    assert (await accept(client, headers, created["token"])).status_code == 200
    return headers


async def _user_id(db_session, email):
    return (await db_session.execute(select(User.id).where(User.email == email))).scalar_one()


async def _owner_deleting(db_session, owner_email, did, recipient_id):
    deadline = datetime.now(UTC) + timedelta(days=30)
    await db_session.execute(
        update(User)
        .where(User.email == owner_email)
        .values(deleted_at=datetime.now(UTC), hard_delete_after=deadline)
    )
    await db_session.execute(
        update(Diary).where(Diary.id == did).values(handover_to_user_id=recipient_id)
    )
    await db_session.commit()
    return deadline


@pytest.mark.asyncio
async def test_shared_diary_says_who_shared_it(client, db_session):
    owner, did = await make_shared_diary(client, "do1@example.com")
    await db_session.execute(
        update(User).where(User.email == "do1@example.com").values(display_name="Andrew")
    )
    await db_session.commit()
    guest = await _join(client, owner, did, "do1g@example.com")
    item = next(
        d for d in (await client.get("/v1/diaries", headers=guest)).json() if d["id"] == did
    )
    assert item["role"] == "viewer"
    assert item["owner_name"] == "Andrew"
    assert item["people"] is None
    assert item["deletion_notice"] is None
    mine = next(
        d for d in (await client.get("/v1/diaries", headers=owner)).json() if d["id"] == did
    )
    assert mine["role"] == "owner"
    assert mine["people"] == {"used": 1, "limit": 3}
    assert mine["deletion_notice"] is None
    got = (await client.get(f"/v1/diaries/{did}", headers=owner)).json()
    assert got["people"] == {"used": 1, "limit": 3}


@pytest.mark.asyncio
async def test_owner_name_falls_back_to_email(client):
    owner, did = await make_shared_diary(client, "do2@example.com")
    guest = await _join(client, owner, did, "do2g@example.com")
    got = (await client.get(f"/v1/diaries/{did}", headers=guest)).json()
    assert got["owner_name"] == "do2@example.com"


@pytest.mark.asyncio
async def test_list_owned_first_then_shared_each_by_name(client):
    owner, did = await make_shared_diary(client, "do3@example.com")
    guest = await _join(client, owner, did, "do3g@example.com")
    await client.post("/v1/diaries", json={"name": "zeta", "timezone": "UTC"}, headers=guest)
    names = [d["name"] for d in (await client.get("/v1/diaries", headers=guest)).json()]
    assert names == ["zeta", "Shared"]


@pytest.mark.asyncio
async def test_members_see_deletion_notice(client, db_session):
    owner, did = await make_shared_diary(client, "do4@example.com", "plus")
    a = await _join(client, owner, did, "do4a@example.com")
    b = await _join(client, owner, did, "do4b@example.com")
    deadline = await _owner_deleting(
        db_session, "do4@example.com", did, await _user_id(db_session, "do4a@example.com")
    )
    na = (await client.get(f"/v1/diaries/{did}", headers=a)).json()["deletion_notice"]
    nb = (await client.get(f"/v1/diaries/{did}", headers=b)).json()["deletion_notice"]
    assert na["becomes_yours"] is True
    assert nb["becomes_yours"] is False
    assert na["goes_to_name"] == "do4a@example.com"
    assert nb["goes_to_name"] == "do4a@example.com"
    assert datetime.fromisoformat(na["deletes_on"]) == deadline
    listed = next(d for d in (await client.get("/v1/diaries", headers=a)).json() if d["id"] == did)
    assert listed["deletion_notice"]["becomes_yours"] is True


@pytest.mark.asyncio
async def test_recipient_can_decline(client, db_session):
    owner, did = await make_shared_diary(client, "do5@example.com", "plus")
    a = await _join(client, owner, did, "do5a@example.com")
    await _owner_deleting(
        db_session, "do5@example.com", did, await _user_id(db_session, "do5a@example.com")
    )
    r = await client.post(f"/v1/diaries/{did}/handover/decline", headers=a)
    assert r.status_code == 204, r.text
    await db_session.commit()
    handover = (
        await db_session.execute(
            select(Diary.handover_to_user_id)
            .where(Diary.id == did)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert handover is None
    notice = (await client.get(f"/v1/diaries/{did}", headers=a)).json()["deletion_notice"]
    assert notice["becomes_yours"] is False
    again = await client.post(f"/v1/diaries/{did}/handover/decline", headers=a)
    assert again.status_code == 404
    assert again.json()["error"]["message"] == "not_found"


@pytest.mark.asyncio
async def test_non_recipient_decline_404(client, db_session):
    owner, did = await make_shared_diary(client, "do6@example.com", "plus")
    await _join(client, owner, did, "do6a@example.com")
    b = await _join(client, owner, did, "do6b@example.com")
    await _owner_deleting(
        db_session, "do6@example.com", did, await _user_id(db_session, "do6a@example.com")
    )
    r = await client.post(f"/v1/diaries/{did}/handover/decline", headers=b)
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "not_found"
    r = await client.post(f"/v1/diaries/{did}/handover/decline", headers=owner)
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "account_pending_deletion"


@pytest.mark.asyncio
async def test_decline_404_when_owner_not_deleting(client, db_session):
    owner, did = await make_shared_diary(client, "do7@example.com", "plus")
    a = await _join(client, owner, did, "do7a@example.com")
    await db_session.execute(
        update(Diary)
        .where(Diary.id == did)
        .values(handover_to_user_id=await _user_id(db_session, "do7a@example.com"))
    )
    await db_session.commit()
    r = await client.post(f"/v1/diaries/{did}/handover/decline", headers=a)
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "not_found"


async def _editor(client, owner, did, email):
    created = await invite(client, owner, did, email, role="editor")
    headers = await register(client, email)
    assert (await accept(client, headers, created["token"])).status_code == 200
    return headers


@pytest.mark.asyncio
async def test_editor_role_follows_owner_plan(client):
    owner, did = await make_shared_diary(client, "do8@example.com", "family")
    editor = await _editor(client, owner, did, "do8e@example.com")
    item = next(
        d for d in (await client.get("/v1/diaries", headers=editor)).json() if d["id"] == did
    )
    assert item["role"] == "editor"
    await set_tier("do8@example.com", "plus")
    item = next(
        d for d in (await client.get("/v1/diaries", headers=editor)).json() if d["id"] == did
    )
    assert item["role"] == "viewer"
    got = (await client.get(f"/v1/diaries/{did}", headers=editor)).json()
    assert got["role"] == "viewer"


@pytest.mark.asyncio
async def test_member_get_has_no_people(client):
    owner, did = await make_shared_diary(client, "do9@example.com")
    guest = await _join(client, owner, did, "do9g@example.com")
    got = (await client.get(f"/v1/diaries/{did}", headers=guest)).json()
    assert got["people"] is None
    assert got["role"] == "viewer"


@pytest.mark.asyncio
async def test_notice_has_no_goes_to_name_without_handover(client, db_session):
    owner, did = await make_shared_diary(client, "do9@example.com", "plus")
    b = await _join(client, owner, did, "do9b@example.com")
    await _owner_deleting(db_session, "do9@example.com", did, None)
    nb = (await client.get(f"/v1/diaries/{did}", headers=b)).json()["deletion_notice"]
    assert nb["becomes_yours"] is False
    assert nb["goes_to_name"] is None


@pytest.mark.asyncio
async def test_viewer_entry_has_no_rule_or_ai_details(client, db_session):
    from app.models import AutoCreationRule, EntryRuleMatch, LLMGeneration

    owner, did = await make_shared_diary(client, "do10@example.com")
    viewer = await _join(client, owner, did, "do10v@example.com")
    r = await client.post(
        f"/v1/diaries/{did}/entries", json={"entry_date": "2025-06-01"}, headers=owner
    )
    eid = r.json()["id"]
    assert (await client.post(f"/v1/entries/{eid}/publish", headers=owner)).status_code == 200
    rule = AutoCreationRule(
        diary_id=uuid.UUID(did),
        name="Soccer",
        condition={"op": "AND", "children": []},
        options={},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()
    db_session.add(EntryRuleMatch(entry_id=uuid.UUID(eid), rule_id=rule.id))
    db_session.add(
        LLMGeneration(
            entry_id=uuid.UUID(eid),
            model="m",
            prompt_hash="h",
            status="failed",
            mode="events",
            error="boom",
        )
    )
    await db_session.commit()

    mine = (await client.get(f"/v1/entries/{eid}", headers=owner)).json()
    assert len(mine["rule_matches"]) == 1
    assert mine["last_generation"] is not None
    seen = (await client.get(f"/v1/entries/{eid}", headers=viewer)).json()
    assert seen["rule_matches"] == []
    assert seen["last_generation"] is None
    listed = (await client.get(f"/v1/diaries/{did}/entries", headers=viewer)).json()
    assert listed[0]["rule_matches"] == []
    assert listed[0]["last_generation"] is None
