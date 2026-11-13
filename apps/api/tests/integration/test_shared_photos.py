"""Shared family can see photos others uploaded (finding C-22).

A photo is readable by its uploader, and by anyone who can read an entry it is
attached to: owner and editors always, viewers once the entry is published.
The bytes are always decrypted with the uploader's key.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import DiaryPermission, User
from tests.fixtures.factories import make_photo
from tests.integration.photo_helpers import read_fixture, upload_photo

PASSWORD = "Password1!"  # noqa: S105


async def _register(client, email):
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _grant(db_session, diary_id, email, role):
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary_id), user_id=user.id, role=role))
    await db_session.commit()


def _gps_jpeg() -> bytes:
    """A small JPEG whose EXIF carries GPS coordinates (47.6 N, 122.3 W)."""
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    gps[3], gps[4] = "W", (122.0, 18.0, 0.0)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 80, 40)).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _gps_of(data: bytes) -> dict:
    return dict(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825))


async def _setup(client, db_session, prefix, published, body=None):
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
    body = body or read_fixture("sample.jpg")
    pid = await upload_photo(client, editor, body)
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
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=auth["editor"])
    assert r.content == body  # the uploader gets the original
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=auth["owner"])
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.size == Image.open(io.BytesIO(body)).size
    assert not _gps_of(r.content)


@pytest.mark.asyncio
async def test_uploader_gets_original_with_gps(client, db_session):
    body = _gps_jpeg()
    assert _gps_of(body)
    auth, pid, _ = await _setup(client, db_session, "upgps", False, body=body)
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=auth["editor"])
    assert r.content == body
    assert _gps_of(r.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "viewer"])
async def test_owner_editor_and_viewer_get_no_gps(client, db_session, role):
    body = _gps_jpeg()
    auth, pid, _ = await _setup(client, db_session, f"nogps-{role}", True, body=body)
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=auth[role])
    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).size == Image.open(io.BytesIO(body)).size
    assert not _gps_of(r.content)


@pytest.mark.asyncio
async def test_other_editor_gets_no_gps(client, db_session):
    body = _gps_jpeg()
    auth, pid, _ = await _setup(client, db_session, "othered", False, body=body)
    second = await _register(client, "othered-second@example.com")
    entry_diary = (await client.get("/v1/diaries", headers=auth["owner"])).json()[0]["id"]
    await _grant(db_session, entry_diary, "othered-second@example.com", "editor")
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=second)
    assert r.status_code == 200
    assert not _gps_of(r.content)


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
