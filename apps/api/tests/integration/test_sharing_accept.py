"""Invitee endpoints: look at an invite and accept it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import DiaryPermission, Invitation, User
from tests.integration.sharing_helpers import accept, invite, make_shared_diary, register


async def _name_owner(db_session, email, name="Andrew"):
    await db_session.execute(update(User).where(User.email == email).values(display_name=name))
    await db_session.commit()


async def _rename_diary(client, headers, did) -> str:
    secret = f"Secret-{uuid.uuid4().hex[:8]}"
    r = await client.patch(f"/v1/diaries/{did}", json={"name": secret}, headers=headers)
    assert r.status_code == 200, r.text
    return secret


@pytest.mark.asyncio
async def test_accept_creates_permission_and_opens(client, db_session):
    owner, did = await make_shared_diary(client, "sa1@example.com")
    await _name_owner(db_session, "sa1@example.com")
    created = await invite(client, owner, did, "guest1@example.com")
    guest = await register(client, "guest1@example.com")

    look = await client.get(f"/v1/invitations/{created['token']}", headers=guest)
    assert look.status_code == 200, look.text
    assert look.json() == {
        "diary_id": did,
        "diary_name": "Shared",
        "owner_name": "Andrew",
        "role": "viewer",
        "status": "pending",
    }

    r = await accept(client, guest, created["token"])
    assert r.status_code == 200, r.text
    assert r.json() == {"diary_id": did}
    listed = (await client.get("/v1/diaries", headers=guest)).json()
    assert did in [d["id"] for d in listed]
    again = await accept(client, guest, created["token"])
    assert again.status_code == 200, again.text
    look2 = await client.get(f"/v1/invitations/{created['token']}", headers=guest)
    assert look2.json()["status"] == "member"
    mine = await client.post("/v1/diaries", json={"name": "Mine", "timezone": "UTC"}, headers=guest)
    assert mine.status_code == 201, mine.text


@pytest.mark.asyncio
async def test_accept_matches_email_case_insensitively(client):
    owner, did = await make_shared_diary(client, "sa2@example.com")
    created = await invite(client, owner, did, " Mom@Example.com ")
    mom = await register(client, "mom@example.com")
    r = await accept(client, mom, created["token"])
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_wrong_email_is_refused_without_diary_name(client, db_session):
    owner, did = await make_shared_diary(client, "sa3@example.com")
    secret = await _rename_diary(client, owner, did)
    await _name_owner(db_session, "sa3@example.com")
    created = await invite(client, owner, did, "mom@example.com")
    other = await register(client, "other@example.com")
    for r in (
        await client.get(f"/v1/invitations/{created['token']}", headers=other),
        await accept(client, other, created["token"]),
    ):
        assert r.status_code == 403, r.text
        d = r.json()["detail"]
        assert d["code"] == "invite_wrong_email"
        assert d["details"] == {"masked_email": "m•••@example.com", "owner_name": "Andrew"}
        assert secret not in r.text
        assert did not in r.text
    perms = (await db_session.execute(select(DiaryPermission))).scalars().all()
    assert perms == []


@pytest.mark.asyncio
async def test_link_works_once(client):
    owner, did = await make_shared_diary(client, "sa4@example.com")
    created = await invite(client, owner, did, "guest4@example.com")
    guest = await register(client, "guest4@example.com")
    assert (await accept(client, guest, created["token"])).status_code == 200
    me = (await client.get("/v1/auth/me", headers=guest)).json()
    left = await client.delete(f"/v1/diaries/{did}/permissions/{me['id']}", headers=guest)
    assert left.status_code == 204, left.text
    r = await accept(client, guest, created["token"])
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "invite_not_found"


@pytest.mark.asyncio
async def test_expired_link_410(client, db_session):
    owner, did = await make_shared_diary(client, "sa5@example.com")
    secret = await _rename_diary(client, owner, did)
    await _name_owner(db_session, "sa5@example.com")
    created = await invite(client, owner, did, "guest5@example.com")
    await db_session.execute(
        update(Invitation).values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    guest = await register(client, "guest5@example.com")
    for r in (
        await client.get(f"/v1/invitations/{created['token']}", headers=guest),
        await accept(client, guest, created["token"]),
    ):
        assert r.status_code == 410, r.text
        d = r.json()["detail"]
        assert d["code"] == "invite_expired"
        assert d["details"] == {"owner_name": "Andrew"}
        assert secret not in r.text
        assert did not in r.text


@pytest.mark.asyncio
async def test_owner_opening_own_invite_is_harmless(client, db_session):
    owner, did = await make_shared_diary(client, "sa6@example.com")
    created = await invite(client, owner, did, "guest6@example.com")
    look = await client.get(f"/v1/invitations/{created['token']}", headers=owner)
    assert look.status_code == 200
    assert look.json()["status"] == "owner"
    assert look.json()["role"] == "owner"
    r = await accept(client, owner, created["token"])
    assert r.status_code == 200
    assert r.json() == {"diary_id": did}
    perms = (await db_session.execute(select(DiaryPermission))).scalars().all()
    assert perms == []
    inv = (await db_session.execute(select(Invitation))).scalar_one()
    assert inv.accepted_at is None


@pytest.mark.asyncio
async def test_revoked_invite_404(client):
    owner, did = await make_shared_diary(client, "sa7@example.com")
    created = await invite(client, owner, did, "guest7@example.com")
    d = await client.delete(f"/v1/diaries/{did}/invitations/{created['id']}", headers=owner)
    assert d.status_code == 204
    guest = await register(client, "guest7@example.com")
    for r in (
        await client.get(f"/v1/invitations/{created['token']}", headers=guest),
        await accept(client, guest, created["token"]),
        await client.get(f"/v1/invitations/{uuid.uuid4().hex}", headers=guest),
    ):
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "invite_not_found"


@pytest.mark.asyncio
async def test_signed_out_gets_401(client):
    owner, did = await make_shared_diary(client, "sa8@example.com")
    created = await invite(client, owner, did, "guest8@example.com")
    for r in (
        await client.get(f"/v1/invitations/{created['token']}"),
        await client.post(f"/v1/invitations/{created['token']}/accept"),
    ):
        assert r.status_code == 401
        assert "Shared" not in r.text
        assert "guest8" not in r.text


@pytest.mark.asyncio
async def test_deleted_diary_invite_404(client, db_session):
    owner, did = await make_shared_diary(client, "sa9@example.com")
    created = await invite(client, owner, did, "guest9@example.com")
    guest = await register(client, "guest9@example.com")
    d = await client.delete(f"/v1/diaries/{did}", headers=owner)
    assert d.status_code in (200, 204), d.text
    for r in (
        await client.get(f"/v1/invitations/{created['token']}", headers=guest),
        await accept(client, guest, created["token"]),
        await client.get(f"/v1/invitations/{created['token']}", headers=owner),
        await accept(client, owner, created["token"]),
    ):
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "invite_not_found"
    perms = (await db_session.execute(select(DiaryPermission))).scalars().all()
    assert perms == []


@pytest.mark.asyncio
async def test_revoke_of_accepted_invite_404_keeps_member(client, db_session):
    owner, did = await make_shared_diary(client, "sa10@example.com")
    created = await invite(client, owner, did, "guest10@example.com")
    guest = await register(client, "guest10@example.com")
    assert (await accept(client, guest, created["token"])).status_code == 200
    for r in (
        await client.delete(f"/v1/diaries/{did}/invitations/{created['id']}", headers=owner),
        await client.post(f"/v1/diaries/{did}/invitations/{created['id']}/new-link", headers=owner),
    ):
        assert r.status_code == 404, r.text
    perms = (await db_session.execute(select(DiaryPermission))).scalars().all()
    assert len(perms) == 1


@pytest.mark.asyncio
async def test_unnamed_owner_is_masked_in_wrong_email_and_expired(client, db_session):
    owner, did = await make_shared_diary(client, "sa30@example.com")
    created = await invite(client, owner, did, "guest30@example.com")
    other = await register(client, "other30@example.com")
    r = await accept(client, other, created["token"])
    assert r.status_code == 403
    assert r.json()["detail"]["details"]["owner_name"] == "s•••@example.com"
    assert "sa30@example.com" not in r.text
    await db_session.execute(
        update(Invitation).values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    guest = await register(client, "guest30@example.com")
    r = await accept(client, guest, created["token"])
    assert r.status_code == 410
    assert r.json()["detail"]["details"]["owner_name"] == "s•••@example.com"
    assert "sa30@example.com" not in r.text
