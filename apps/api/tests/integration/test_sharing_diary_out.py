"""What diary responses tell members: who shared it, counts, deletion notice, decline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import Diary, User
from tests.integration.sharing_helpers import (
    accept,
    invite,
    make_shared_diary,
    register,
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
    assert datetime.fromisoformat(na["deletes_on"]) == deadline.replace(
        microsecond=deadline.microsecond
    )
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


@pytest.mark.asyncio
async def test_non_recipient_decline_404(client, db_session):
    owner, did = await make_shared_diary(client, "do6@example.com", "plus")
    await _join(client, owner, did, "do6a@example.com")
    b = await _join(client, owner, did, "do6b@example.com")
    await _owner_deleting(
        db_session, "do6@example.com", did, await _user_id(db_session, "do6a@example.com")
    )
    assert (await client.post(f"/v1/diaries/{did}/handover/decline", headers=b)).status_code == 404
    assert (
        await client.post(f"/v1/diaries/{did}/handover/decline", headers=owner)
    ).status_code in (403, 404)


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
    assert (await client.post(f"/v1/diaries/{did}/handover/decline", headers=a)).status_code == 404
