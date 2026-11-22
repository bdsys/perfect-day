"""Choosing who gets each shared diary when an account is deleted."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from app.models import Diary, User
from tests.integration.sharing_helpers import accept, invite, make_shared_diary, register

ACCOUNT = "/v1/auth/account"
OPTIONS = "/v1/auth/account/handover-options"
RESTORE = "/v1/auth/account/restore"


async def _share(client, owner_email, member_email, tier="free"):
    owner, did = await make_shared_diary(client, owner_email, tier)
    created = await invite(client, owner, did, member_email)
    member = await register(client, member_email)
    assert (await accept(client, member, created["token"])).status_code == 200
    return owner, member, did


async def _user(db_session, email) -> User:
    return (
        await db_session.execute(
            select(User).where(User.email == email).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _handover(db_session, did) -> uuid.UUID | None:
    return (
        await db_session.execute(
            select(Diary.handover_to_user_id)
            .where(Diary.id == uuid.UUID(did))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_handover_options_list_shared_diaries_only(client, db_session):
    owner, _member, did = await _share(client, "ho1@example.com", "ho1m@example.com", "plus")
    solo = await client.post("/v1/diaries", json={"name": "Solo", "timezone": "UTC"}, headers=owner)
    assert solo.status_code == 201, solo.text
    member_user = await _user(db_session, "ho1m@example.com")

    r = await client.get(OPTIONS, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "diary_id": did,
            "diary_name": "Shared",
            "members": [{"user_id": str(member_user.id), "name": "ho1m@example.com"}],
        }
    ]


@pytest.mark.asyncio
async def test_delete_with_handover_records_choice(client, db_session):
    owner, _member, did = await _share(client, "ho2@example.com", "ho2m@example.com")
    member_user = await _user(db_session, "ho2m@example.com")

    r = await client.request(
        "DELETE", ACCOUNT, json={"handovers": {did: str(member_user.id)}}, headers=owner
    )
    assert r.status_code == 200, r.text
    assert (await _user(db_session, "ho2@example.com")).deleted_at is not None
    assert await _handover(db_session, did) == member_user.id


@pytest.mark.asyncio
async def test_delete_without_body_unchanged(client, db_session):
    owner, _member, did = await _share(client, "ho3@example.com", "ho3m@example.com")

    r = await client.delete(ACCOUNT, headers=owner)
    assert r.status_code == 200, r.text
    assert "deletion_scheduled_for" in r.json()
    assert (await _user(db_session, "ho3@example.com")).deleted_at is not None
    assert await _handover(db_session, did) is None


@pytest.mark.asyncio
async def test_handover_to_non_member_422_and_account_not_deleted(client, db_session):
    owner, _member, did = await _share(client, "ho4@example.com", "ho4m@example.com")
    await register(client, "ho4x@example.com")
    stranger = await _user(db_session, "ho4x@example.com")

    r = await client.request(
        "DELETE", ACCOUNT, json={"handovers": {did: str(stranger.id)}}, headers=owner
    )
    assert r.status_code == 422, r.text
    assert "invalid_handover" in r.text
    assert (await _user(db_session, "ho4@example.com")).deleted_at is None
    assert await _handover(db_session, did) is None
    # Still signed in: tokens were not revoked.
    assert (await client.get("/v1/auth/me", headers=owner)).status_code == 200


@pytest.mark.asyncio
async def test_handover_for_someone_elses_diary_422(client, db_session):
    owner, _member, _did = await _share(client, "ho5@example.com", "ho5m@example.com")
    _o2, member2, did2 = await _share(client, "ho5b@example.com", "ho5bm@example.com")
    m2 = await _user(db_session, "ho5bm@example.com")
    r = await client.request(
        "DELETE", ACCOUNT, json={"handovers": {did2: str(m2.id)}}, headers=owner
    )
    assert r.status_code == 422, r.text
    assert (await _user(db_session, "ho5@example.com")).deleted_at is None
    assert await _handover(db_session, did2) is None


@pytest.mark.asyncio
async def test_handover_to_pending_deletion_member_422(client, db_session):
    owner, member, did = await _share(client, "ho6@example.com", "ho6m@example.com")
    member_user = await _user(db_session, "ho6m@example.com")
    assert (await client.delete(ACCOUNT, headers=member)).status_code == 200
    r = await client.request(
        "DELETE", ACCOUNT, json={"handovers": {did: str(member_user.id)}}, headers=owner
    )
    assert r.status_code == 422, r.text
    opts = await client.get(OPTIONS, headers=owner)
    assert opts.json() == []


@pytest.mark.asyncio
async def test_restore_clears_handover_choice(client, db_session):
    owner, _member, did = await _share(client, "ho7@example.com", "ho7m@example.com")
    member_user = await _user(db_session, "ho7m@example.com")
    assert (
        await client.request(
            "DELETE", ACCOUNT, json={"handovers": {did: str(member_user.id)}}, headers=owner
        )
    ).status_code == 200
    assert await _handover(db_session, did) == member_user.id

    r = await client.post(RESTORE, headers=owner)
    assert r.status_code == 204, r.text
    assert await _handover(db_session, did) is None


@pytest.mark.asyncio
async def test_new_request_replaces_stale_choice(client, db_session):
    owner, _member, did = await _share(client, "ho8@example.com", "ho8m@example.com")
    member_user = await _user(db_session, "ho8m@example.com")
    await db_session.execute(
        update(Diary).where(Diary.id == uuid.UUID(did)).values(handover_to_user_id=member_user.id)
    )
    await db_session.commit()
    assert (await client.delete(ACCOUNT, headers=owner)).status_code == 200
    assert await _handover(db_session, did) is None
