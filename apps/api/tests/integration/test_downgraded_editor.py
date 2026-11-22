"""An editor whose owner is not on Family acts as a viewer (stored role kept)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from app.models import DiaryPermission, User
from tests.integration.photo_helpers import read_fixture, upload_photo

PASSWORD = "Password1!"  # noqa: S105


async def _register(client, email):
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_tier(db_session, email, tier):
    await db_session.execute(update(User).where(User.email == email).values(subscription_tier=tier))
    await db_session.commit()


async def _setup(client, db_session, prefix, tier="plus"):
    owner_email = f"{prefix}-owner@example.com"
    editor_email = f"{prefix}-editor@example.com"
    owner = await _register(client, owner_email)
    editor = await _register(client, editor_email)
    await _set_tier(db_session, owner_email, tier)
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=owner)
    ).json()
    user = (await db_session.execute(select(User).where(User.email == editor_email))).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=user.id, role="editor"))
    await db_session.commit()
    draft = (
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-16"}, headers=owner
        )
    ).json()
    pid = await upload_photo(client, owner, read_fixture("sample.jpg"))
    r = await client.post(
        f"/v1/entries/{draft['id']}/photos", json={"photo_id": pid}, headers=owner
    )
    assert r.status_code == 201, r.text
    published = (
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-17"}, headers=owner
        )
    ).json()
    r = await client.post(f"/v1/entries/{published['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    return owner, editor, diary, draft, published, pid


@pytest.mark.asyncio
async def test_downgraded_editor_cannot_see_drafts(client, db_session):
    _, editor, diary, draft, published, _ = await _setup(client, db_session, "dd1")
    r = await client.get(f"/v1/diaries/{diary['id']}/entries", headers=editor)
    assert r.status_code == 200, r.text
    ids = {e["id"] for e in r.json()}
    assert draft["id"] not in ids
    assert published["id"] in ids
    r = await client.get(f"/v1/entries/{draft['id']}", headers=editor)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_downgraded_editor_cannot_read_draft_photo(client, db_session):
    _, editor, _, _, _, pid = await _setup(client, db_session, "dd2")
    r = await client.get(f"/v1/photos/{pid}", headers=editor)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_downgraded_editor_cannot_edit(client, db_session):
    _, editor, _, _, published, _ = await _setup(client, db_session, "dd3")
    r = await client.patch(f"/v1/entries/{published['id']}", json={"title": "x"}, headers=editor)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_family_editor_still_edits(client, db_session):
    _, editor, _, _, published, pid = await _setup(client, db_session, "dd4", tier="family")
    r = await client.patch(f"/v1/entries/{published['id']}", json={"title": "x"}, headers=editor)
    assert r.status_code == 200, r.text
    r = await client.get(f"/v1/photos/{pid}", headers=editor)
    assert r.status_code == 200, r.text
