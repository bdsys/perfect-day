"""Owner endpoints: invites, members, leaving, and the plan limits on them."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.security import hash_token
from app.models import Diary, DiaryPermission, Invitation, User
from tests.integration.sharing_helpers import invite, make_shared_diary, register, set_tier


async def _add_member(db_session, diary_id, email, role="viewer"):
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary_id), user_id=user.id, role=role))
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_invite_returns_link_token_once(client, db_session):
    owner, did = await make_shared_diary(client, "so1@example.com")
    created = await invite(client, owner, did, "Guest@Example.com")
    assert created["token"]
    assert created["invited_email"] == "guest@example.com"
    assert created["status"] == "pending"
    listed = (await client.get(f"/v1/diaries/{did}/invitations", headers=owner)).json()
    assert len(listed) == 1
    assert listed[0]["token"] is None
    rows = (await db_session.execute(select(Invitation))).scalars().all()
    assert [r.token for r in rows] == [hash_token(created["token"])]


@pytest.mark.asyncio
async def test_free_limit_counts_members_and_pending(client):
    owner, did = await make_shared_diary(client, "so2@example.com")
    for i in range(3):
        await invite(client, owner, did, f"g{i}@example.com")
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "g3@example.com"}, headers=owner
    )
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "tier_limit"
    assert d["details"]["source"] == "people"
    assert d["details"]["limit"] == 3
    assert d["details"]["required_tier"] == "plus"


@pytest.mark.asyncio
async def test_expired_invites_do_not_count(client, db_session):
    owner, did = await make_shared_diary(client, "so3@example.com")
    for i in range(3):
        await invite(client, owner, did, f"g{i}@example.com")
    await db_session.execute(
        update(Invitation)
        .where(Invitation.invited_email == "g0@example.com")
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "g3@example.com"}, headers=owner
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_new_link_on_expired_invite_checks_limit(client, db_session):
    owner, did = await make_shared_diary(client, "so4@example.com")
    made = [await invite(client, owner, did, f"g{i}@example.com") for i in range(3)]
    await db_session.execute(
        update(Invitation)
        .where(Invitation.id == uuid.UUID(made[0]["id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    await invite(client, owner, did, "g3@example.com")  # fills the freed seat
    listed = (await client.get(f"/v1/diaries/{did}/invitations", headers=owner)).json()
    assert {i["status"] for i in listed} == {"pending", "expired"}
    r = await client.post(f"/v1/diaries/{did}/invitations/{made[0]['id']}/new-link", headers=owner)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "people"


@pytest.mark.asyncio
async def test_new_link_replaces_token(client, db_session):
    owner, did = await make_shared_diary(client, "so5@example.com")
    first = await invite(client, owner, did, "g@example.com")
    r = await client.post(f"/v1/diaries/{did}/invitations/{first['id']}/new-link", headers=owner)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["token"] and new["token"] != first["token"]
    assert new["status"] == "pending"
    stored = (await db_session.execute(select(Invitation.token))).scalars().all()
    assert stored == [hash_token(new["token"])]
    assert hash_token(first["token"]) not in stored


@pytest.mark.asyncio
async def test_delete_invitation(client):
    owner, did = await make_shared_diary(client, "so5b@example.com")
    inv = await invite(client, owner, did, "g@example.com")
    r = await client.delete(f"/v1/diaries/{did}/invitations/{inv['id']}", headers=owner)
    assert r.status_code == 204
    r = await client.delete(f"/v1/diaries/{did}/invitations/{inv['id']}", headers=owner)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_editor_invite_needs_family(client):
    owner, did = await make_shared_diary(client, "so6@example.com", tier="plus")
    r = await client.post(
        f"/v1/diaries/{did}/invitations",
        json={"email": "e@example.com", "role": "editor"},
        headers=owner,
    )
    assert r.status_code == 403, r.text
    d = r.json()["detail"]["details"]
    assert d["source"] == "editor"
    assert d["required_tier"] == "family"
    owner2, did2 = await make_shared_diary(client, "so6b@example.com", tier="family")
    inv = await invite(client, owner2, did2, "e@example.com", role="editor")
    assert inv["role"] == "editor"


@pytest.mark.asyncio
async def test_cannot_invite_self_or_member_or_twice(client, db_session):
    owner, did = await make_shared_diary(client, "so7@example.com", tier="plus")
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "SO7@example.com"}, headers=owner
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["message"] == "cannot_invite_self"

    await register(client, "m7@example.com")
    await _add_member(db_session, did, "m7@example.com")
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "M7@example.com"}, headers=owner
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["message"] == "already_member"

    await invite(client, owner, did, "x7@example.com")
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "X7@Example.com"}, headers=owner
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["message"] == "already_invited"


@pytest.mark.asyncio
async def test_non_owner_gets_403_on_owner_endpoints(client, db_session):
    owner, did = await make_shared_diary(client, "so8@example.com")
    viewer = await register(client, "v8@example.com")
    await _add_member(db_session, did, "v8@example.com")
    for path in ("invitations", "permissions"):
        r = await client.get(f"/v1/diaries/{did}/{path}", headers=viewer)
        assert r.status_code == 403, (path, r.text)
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "z@example.com"}, headers=viewer
    )
    assert r.status_code == 403
    stranger = await register(client, "s8@example.com")
    r = await client.get(f"/v1/diaries/{did}/permissions", headers=stranger)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_member_can_leave_and_owner_can_remove(client, db_session):
    owner, did = await make_shared_diary(client, "so9@example.com")
    a = await register(client, "a9@example.com")
    await register(client, "b9@example.com")
    ua = await _add_member(db_session, did, "a9@example.com")
    ub = await _add_member(db_session, did, "b9@example.com")
    r = await client.delete(f"/v1/diaries/{did}/permissions/{ub.id}", headers=a)
    assert r.status_code == 403, r.text
    r = await client.delete(f"/v1/diaries/{did}/permissions/{ua.id}", headers=a)
    assert r.status_code == 204, r.text
    r = await client.delete(f"/v1/diaries/{did}/permissions/{ub.id}", headers=owner)
    assert r.status_code == 204, r.text
    r = await client.delete(f"/v1/diaries/{did}/permissions/{ub.id}", headers=owner)
    assert r.status_code == 404
    assert (await client.get(f"/v1/diaries/{did}/permissions", headers=owner)).json() == []


@pytest.mark.asyncio
async def test_leaving_clears_handover_choice(client, db_session):
    owner, did = await make_shared_diary(client, "so10@example.com")
    m = await register(client, "m10@example.com")
    um = await _add_member(db_session, did, "m10@example.com")
    await db_session.execute(
        update(Diary).where(Diary.id == uuid.UUID(did)).values(handover_to_user_id=um.id)
    )
    await db_session.commit()
    r = await client.delete(f"/v1/diaries/{did}/permissions/{um.id}", headers=m)
    assert r.status_code == 204, r.text
    db_session.expire_all()
    diary = (await db_session.execute(select(Diary).where(Diary.id == uuid.UUID(did)))).scalar_one()
    assert diary.handover_to_user_id is None


@pytest.mark.asyncio
async def test_patch_member_role(client, db_session):
    owner, did = await make_shared_diary(client, "so11@example.com", tier="plus")
    await register(client, "m11@example.com")
    um = await _add_member(db_session, did, "m11@example.com")
    r = await client.patch(
        f"/v1/diaries/{did}/permissions/{um.id}", json={"role": "editor"}, headers=owner
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "editor"
    await set_tier("so11@example.com", "family")
    r = await client.patch(
        f"/v1/diaries/{did}/permissions/{um.id}", json={"role": "editor"}, headers=owner
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "editor" and r.json()["effective_role"] == "editor"
    r = await client.patch(
        f"/v1/diaries/{did}/permissions/{uuid.uuid4()}", json={"role": "viewer"}, headers=owner
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_downgrade_keeps_people_and_blocks_new_invites(client, db_session):
    owner, did = await make_shared_diary(client, "so12@example.com", tier="plus")
    for i in range(4):
        await register(client, f"v12-{i}@example.com")
        await _add_member(db_session, did, f"v12-{i}@example.com")
    await register(client, "ed12@example.com")
    await _add_member(db_session, did, "ed12@example.com", role="editor")
    await set_tier("so12@example.com", "free")
    r = await client.get(f"/v1/diaries/{did}/permissions", headers=owner)
    assert r.status_code == 200, r.text
    members = r.json()
    assert len(members) == 5
    editor = next(m for m in members if m["email"] == "ed12@example.com")
    assert editor["role"] == "editor"
    assert editor["effective_role"] == "viewer"
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "new@example.com"}, headers=owner
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "people"


async def _expire(db_session, invitation_id):
    await db_session.execute(
        update(Invitation)
        .where(Invitation.id == uuid.UUID(invitation_id))
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_concurrent_invites_for_same_email_make_one(client, db_session):
    owner, did = await make_shared_diary(client, "so13@example.com")
    url = f"/v1/diaries/{did}/invitations"
    rs = await asyncio.gather(
        *[client.post(url, json={"email": "dup@example.com"}, headers=owner) for _ in range(2)]
    )
    assert sorted(r.status_code for r in rs) == [201, 409], [r.text for r in rs]
    rows = (await db_session.execute(select(Invitation))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_new_link_on_replaced_expired_invite_is_gone_when_reinvited(client, db_session):
    owner, did = await make_shared_diary(client, "so14@example.com", tier="plus")
    old = await invite(client, owner, did, "mom@example.com")
    await _expire(db_session, old["id"])
    await invite(client, owner, did, "mom@example.com")
    r = await client.post(f"/v1/diaries/{did}/invitations/{old['id']}/new-link", headers=owner)
    # Re-inviting replaced the expired row, so its id is gone.
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_new_link_on_expired_invite_refused_when_already_member(client, db_session):
    owner, did = await make_shared_diary(client, "so15@example.com", tier="plus")
    old = await invite(client, owner, did, "mom@example.com")
    await _expire(db_session, old["id"])
    await register(client, "mom@example.com")
    await _add_member(db_session, did, "mom@example.com")
    r = await client.post(f"/v1/diaries/{did}/invitations/{old['id']}/new-link", headers=owner)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["message"] == "already_member"


@pytest.mark.asyncio
async def test_new_link_on_editor_invite_checks_plan_before_room(client, db_session):
    owner, did = await make_shared_diary(client, "so16@example.com", tier="family")
    inv = await invite(client, owner, did, "ed@example.com", role="editor")
    await _expire(db_session, inv["id"])
    await set_tier("so16@example.com", "plus")
    r = await client.post(f"/v1/diaries/{did}/invitations/{inv['id']}/new-link", headers=owner)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "editor"


@pytest.mark.asyncio
async def test_viewer_gets_403_on_patch_delete_invitation_and_new_link(client, db_session):
    owner, did = await make_shared_diary(client, "so17@example.com")
    viewer = await register(client, "v17@example.com")
    uv = await _add_member(db_session, did, "v17@example.com")
    inv = await invite(client, owner, did, "g@example.com")
    r = await client.patch(
        f"/v1/diaries/{did}/permissions/{uv.id}", json={"role": "viewer"}, headers=viewer
    )
    assert r.status_code == 403, r.text
    r = await client.delete(f"/v1/diaries/{did}/invitations/{inv['id']}", headers=viewer)
    assert r.status_code == 403, r.text
    r = await client.post(f"/v1/diaries/{did}/invitations/{inv['id']}/new-link", headers=viewer)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_owner_deleting_own_user_id_is_404(client, db_session):
    owner, did = await make_shared_diary(client, "so18@example.com")
    me = (
        await db_session.execute(select(User).where(User.email == "so18@example.com"))
    ).scalar_one()
    r = await client.delete(f"/v1/diaries/{did}/permissions/{me.id}", headers=owner)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_invitation_from_another_diary_is_404(client):
    owner, did = await make_shared_diary(client, "so19@example.com", tier="plus")
    r = await client.post("/v1/diaries", json={"name": "Two", "timezone": "UTC"}, headers=owner)
    other = r.json()["id"]
    inv = await invite(client, owner, other, "g@example.com")
    r = await client.delete(f"/v1/diaries/{did}/invitations/{inv['id']}", headers=owner)
    assert r.status_code == 404, r.text
    r = await client.post(f"/v1/diaries/{did}/invitations/{inv['id']}/new-link", headers=owner)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_invite_on_read_only_diary_refused(client):
    owner, _ = await make_shared_diary(client, "so20@example.com", tier="plus")
    r = await client.post("/v1/diaries", json={"name": "Two", "timezone": "UTC"}, headers=owner)
    second = r.json()["id"]
    await set_tier("so20@example.com", "free")
    r = await client.post(
        f"/v1/diaries/{second}/invitations", json={"email": "g@example.com"}, headers=owner
    )
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "tier_limit"
    assert d["details"]["source"] == "diary_read_only"


@pytest.mark.asyncio
async def test_free_limit_counts_two_members_plus_one_pending(client, db_session):
    owner, did = await make_shared_diary(client, "so21@example.com")
    for i in range(2):
        await register(client, f"m21-{i}@example.com")
        await _add_member(db_session, did, f"m21-{i}@example.com")
    await invite(client, owner, did, "p21@example.com")
    r = await client.post(
        f"/v1/diaries/{did}/invitations", json={"email": "x21@example.com"}, headers=owner
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "people"


@pytest.mark.asyncio
async def test_share_lock_is_taken_before_the_duplicate_checks(client):
    """Deterministic pin: the concurrent test above cannot prove ordering by itself."""
    from unittest.mock import patch

    from app.routers.v1 import sharing as router_mod

    calls: list[str] = []
    real_lock = router_mod.lock_diary_sharing
    real_check = router_mod._ensure_not_member_or_invited

    async def lock(*a, **kw):
        calls.append("lock")
        return await real_lock(*a, **kw)

    async def check(*a, **kw):
        calls.append("check")
        return await real_check(*a, **kw)

    owner, did = await make_shared_diary(client, "so22@example.com")
    with (
        patch.object(router_mod, "lock_diary_sharing", lock),
        patch.object(router_mod, "_ensure_not_member_or_invited", check),
    ):
        await invite(client, owner, did, "g@example.com")
    assert calls == ["lock", "check"]


@pytest.mark.asyncio
async def test_reinviting_an_expired_email_replaces_the_old_row(client, db_session):
    owner, did = await make_shared_diary(client, "so40@example.com")
    await invite(client, owner, did, "g40@example.com")
    await db_session.execute(
        update(Invitation).values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    await invite(client, owner, did, "G40@example.com")
    listed = (await client.get(f"/v1/diaries/{did}/invitations", headers=owner)).json()
    assert [i["invited_email"] for i in listed] == ["g40@example.com"]
