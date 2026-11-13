"""Shared family can see photos others uploaded (finding C-22).

A photo is readable by its uploader, and by anyone who can read an entry it is
attached to: owner and editors always, viewers once the entry is published.
The bytes are always decrypted with the uploader's key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import DiaryPermission, User
from tests.fixtures.factories import make_photo
from tests.integration.test_photos import _read_fixture, _upload_and_finalize

PASSWORD = "Password1!"  # noqa: S105


async def _register(client, email):
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _grant(db_session, diary_id, email, role):
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary_id), user_id=user.id, role=role))
    await db_session.commit()


async def _setup(client, db_session, prefix, published):
    """Owner's entry with a photo the editor uploaded and attached."""
    owner = await _register(client, f"{prefix}-owner@example.com")
    editor = await _register(client, f"{prefix}-editor@example.com")
    viewer = await _register(client, f"{prefix}-viewer@example.com")
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=owner)
    ).json()
    await _grant(db_session, diary["id"], f"{prefix}-editor@example.com", "editor")
    await _grant(db_session, diary["id"], f"{prefix}-viewer@example.com", "viewer")
    entry = (
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-16"}, headers=owner
        )
    ).json()
    body = _read_fixture("sample.jpg")
    pid = await _upload_and_finalize(client, editor, body)
    r = await client.post(
        f"/v1/entries/{entry['id']}/photos", json={"photo_id": pid}, headers=editor
    )
    assert r.status_code == 201, r.text
    if published:
        r = await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner)
        assert r.status_code == 200, r.text
    return {"owner": owner, "editor": editor, "viewer": viewer}, pid, body


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("role", ["owner", "editor", "viewer"])
@pytest.mark.parametrize("kind", ["thumb", "full"])
async def test_who_can_see_a_shared_photo(client, db_session, role, published, kind):
    auth, pid, _ = await _setup(client, db_session, f"s-{role}-{int(published)}-{kind}", published)
    expected = 404 if (role == "viewer" and not published) else 200
    r = await client.get(f"/v1/photos/{pid}?kind={kind}", headers=auth[role])
    assert r.status_code == expected, r.text


@pytest.mark.asyncio
async def test_full_bytes_decrypt_with_uploaders_key(client, db_session):
    auth, pid, body = await _setup(client, db_session, "bytes", published=False)
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=auth["owner"])
    assert r.status_code == 200
    assert r.content == body


@pytest.mark.asyncio
async def test_stranger_gets_404(client, db_session):
    _, pid, _ = await _setup(client, db_session, "stranger", published=True)
    stranger = await _register(client, "stranger@example.com")
    r = await client.get(f"/v1/photos/{pid}", headers=stranger)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_library_near_date_orders_closest_first(client, db_session):
    auth = await _register(client, "near@example.com")
    user = (
        await db_session.execute(select(User).where(User.email == "near@example.com"))
    ).scalar_one()
    base = datetime(2026, 6, 15, 12, tzinfo=UTC)
    far = await make_photo(db_session, user=user, taken_at=base + timedelta(days=30))
    undated = await make_photo(db_session, user=user)
    near = await make_photo(db_session, user=user, taken_at=base - timedelta(days=1))
    mid = await make_photo(db_session, user=user, taken_at=base + timedelta(days=10))
    await db_session.commit()
    r = await client.get("/v1/photos?near=2026-06-15", headers=auth)
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()] == [
        str(near.id), str(mid.id), str(far.id), str(undated.id)
    ]
