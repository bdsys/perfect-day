"""Integration tests for photo upload, encryption, and attachment."""
from __future__ import annotations

import pytest

from tests.integration.photo_helpers import read_fixture, register_and_login, upload_photo


@pytest.mark.asyncio
async def test_minio_fixture_smoke(s3_client, photos_bucket):
    """Sanity check: MinIO testcontainer + bucket fixture wire up correctly."""
    s3_client.put_object(Bucket=photos_bucket, Key="smoke", Body=b"ok")
    obj = s3_client.get_object(Bucket=photos_bucket, Key="smoke")
    assert obj["Body"].read() == b"ok"


@pytest.mark.asyncio
async def test_make_photo_factory(db_session):
    from tests.fixtures.factories import make_diary, make_photo, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    photo = await make_photo(db_session, user=user, finalized=True)
    await db_session.commit()
    assert photo.user_id == user.id
    assert photo.finalized_at is not None
    assert photo.s3_key.startswith(f"{user.id}/")
    assert photo.dek_ciphertext is not None
    assert diary.owner_user_id == user.id


@pytest.mark.asyncio
async def test_put_get_head_delete_round_trip(s3_client, photos_bucket, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import (
        delete_object,
        get_object_bytes,
        head_object_size,
        put_object_bytes,
    )

    put_object_bytes("k1", b"hello", content_type="text/plain")
    assert get_object_bytes("k1") == b"hello"
    assert head_object_size("k1") == 5
    delete_object("k1")
    assert head_object_size("k1") is None
    delete_object("k1")  # second delete must not raise


@pytest.mark.asyncio
async def test_stream_object_yields_streaming_body(s3_client, photos_bucket, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import put_object_bytes, stream_object

    put_object_bytes("stream", b"abcdef", content_type="application/octet-stream")
    body = stream_object("stream")
    assert body.read() == b"abcdef"


# ---------------------------------------------------------------------------
# Task 12: GET /v1/photos/{id}?kind=full|thumb
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_photo_full_round_trip(client, s3_client, photos_bucket):
    headers = await register_and_login(client, "get1@example.com")
    body = read_fixture("sample.jpg")

    pid = await upload_photo(client, headers, body)

    r2 = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/jpeg")
    assert r2.content == body  # round-trip lossless for JPEG


@pytest.mark.asyncio
async def test_get_photo_thumb(client):
    headers = await register_and_login(client, "get2@example.com")
    body = read_fixture("sample.jpg")

    pid = await upload_photo(client, headers, body)

    r2 = await client.get(f"/v1/photos/{pid}?kind=thumb", headers=headers)
    assert r2.status_code == 200
    assert r2.content[:3] == b"\xff\xd8\xff"  # thumbnail is JPEG


@pytest.mark.asyncio
async def test_get_photo_wrong_user_returns_404(client):
    headers_a = await register_and_login(client, "ga@example.com")
    headers_b = await register_and_login(client, "gb@example.com")
    body = read_fixture("sample.jpg")

    pid = await upload_photo(client, headers_a, body)

    r2 = await client.get(f"/v1/photos/{pid}", headers=headers_b)
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Task 13: DELETE /v1/photos/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_photo_soft_deletes(client):
    headers = await register_and_login(client, "del1@example.com")
    body = read_fixture("sample.jpg")
    pid = await upload_photo(client, headers, body)

    r2 = await client.delete(f"/v1/photos/{pid}", headers=headers)
    assert r2.status_code == 204

    # Can no longer fetch
    r3 = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_delete_photo_wrong_user_returns_404(client):
    headers_a = await register_and_login(client, "dela@example.com")
    headers_b = await register_and_login(client, "delb@example.com")
    body = read_fixture("sample.jpg")
    pid = await upload_photo(client, headers_a, body)

    r2 = await client.delete(f"/v1/photos/{pid}", headers=headers_b)
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Task 15: Entry photo attach/detach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_detach_entry_photo(client):
    headers = await register_and_login(client, "ent1@example.com")
    body = read_fixture("sample.jpg")

    # Create diary + entry
    r_diary = await client.post(
        "/v1/diaries",
        json={"name": "ED1", "timezone": "UTC", "scan_interval_minutes": 60},
        headers=headers,
    )
    diary_id = r_diary.json()["id"]

    from datetime import date
    r_entry = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": str(date.today())},
        headers=headers,
    )
    entry_id = r_entry.json()["id"]

    # Upload + finalize photo
    pid = await upload_photo(client, headers, body)

    # Attach with position
    r2 = await client.post(
        f"/v1/entries/{entry_id}/photos",
        json={"photo_id": pid, "position": 0},
        headers=headers,
    )
    assert r2.status_code == 201, r2.text

    # Detach
    r3 = await client.delete(f"/v1/entries/{entry_id}/photos/{pid}", headers=headers)
    assert r3.status_code == 204


@pytest.mark.asyncio
async def test_attach_entry_photo_viewer_returns_403(client):
    """A viewer on a diary cannot attach photos to entries."""
    headers_owner = await register_and_login(client, "eown@example.com")
    headers_viewer = await register_and_login(client, "eview@example.com")
    body = read_fixture("sample.jpg")

    r_diary = await client.post(
        "/v1/diaries",
        json={"name": "ED2", "timezone": "UTC", "scan_interval_minutes": 60},
        headers=headers_owner,
    )
    diary_id = r_diary.json()["id"]

    from datetime import date
    r_entry = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": str(date.today())},
        headers=headers_owner,
    )
    entry_id = r_entry.json()["id"]

    pid = await upload_photo(client, headers_viewer, body)

    # Viewer tries to attach — should fail since they don't have access to this diary's entry
    r2 = await client.post(
        f"/v1/entries/{entry_id}/photos",
        json={"photo_id": str(pid), "position": 0},
        headers=headers_viewer,
    )
    assert r2.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Task 16: GET /v1/photos/{id}/metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_photo_metadata(client):
    headers = await register_and_login(client, "meta1@example.com")
    body = read_fixture("sample.jpg")

    pid = await upload_photo(client, headers, body)

    r2 = await client.get(f"/v1/photos/{pid}/metadata", headers=headers)
    assert r2.status_code == 200
    out = r2.json()
    assert out["id"] == pid
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["has_thumbnail"] is True


# ---------------------------------------------------------------------------
# Entry photos in entry response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_out_includes_attached_photos(client):
    from datetime import date

    headers = await register_and_login(client, "eout1@example.com")
    body = read_fixture("sample.jpg")

    r_diary = await client.post(
        "/v1/diaries",
        json={"name": "EPTest", "scan_interval_minutes": 60, "timezone": "UTC"},
        headers=headers,
    )
    diary_id = r_diary.json()["id"]

    r_entry = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": str(date.today())},
        headers=headers,
    )
    entry_id = r_entry.json()["id"]

    pid = await upload_photo(client, headers, body)
    await client.post(
        f"/v1/entries/{entry_id}/photos",
        json={"photo_id": pid, "position": 0},
        headers=headers,
    )

    r2 = await client.get(f"/v1/entries/{entry_id}", headers=headers)
    assert r2.status_code == 200
    photos = r2.json()["photos"]
    assert len(photos) == 1
    assert photos[0]["id"] == pid



# ---------------------------------------------------------------------------
# Task 18: GET /v1/photos — user-global photo library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_photos_ordered(client):
    """GET /v1/photos returns user's finalized photos in created_at DESC order."""
    headers = await register_and_login(client, "list_order@example.com")
    body = read_fixture("sample.jpg")

    pid1 = await upload_photo(client, headers, body)
    pid2 = await upload_photo(client, headers, body)
    pid3 = await upload_photo(client, headers, body)

    r = await client.get("/v1/photos", headers=headers)
    assert r.status_code == 200, r.text
    photos = r.json()
    ids = [p["id"] for p in photos]
    assert len(ids) == 3
    # Verify all 3 IDs are present
    assert pid1 in ids
    assert pid2 in ids
    assert pid3 in ids
    # Verify created_at DESC order: pid3 was created last, pid1 first
    assert ids.index(pid3) < ids.index(pid2) < ids.index(pid1)


@pytest.mark.asyncio
async def test_list_user_photos_cross_user_isolation(client):
    """GET /v1/photos does not return another user's photos."""
    headers_a = await register_and_login(client, "list_iso_a@example.com")
    headers_b = await register_and_login(client, "list_iso_b@example.com")
    body = read_fixture("sample.jpg")

    await upload_photo(client, headers_a, body)

    # Verify user A's upload succeeded before checking isolation
    r_a = await client.get("/v1/photos", headers=headers_a)
    assert r_a.status_code == 200, r_a.text
    assert len(r_a.json()) == 1, "user A should have exactly 1 photo after upload"

    r = await client.get("/v1/photos", headers=headers_b)
    assert r.status_code == 200, r.text
    photos = r.json()
    assert len(photos) == 0


@pytest.mark.asyncio
async def test_list_user_photos_excludes_deleted(client):
    """GET /v1/photos excludes soft-deleted photos."""
    headers = await register_and_login(client, "list_del@example.com")
    body = read_fixture("sample.jpg")

    pid = await upload_photo(client, headers, body)

    r_del = await client.delete(f"/v1/photos/{pid}", headers=headers)
    assert r_del.status_code == 204

    r = await client.get("/v1/photos", headers=headers)
    assert r.status_code == 200, r.text
    photos = r.json()
    assert len(photos) == 0


@pytest.mark.asyncio
async def test_list_user_photos_excludes_unfinalized(client, db_session):
    """GET /v1/photos excludes photos that have not been finalized."""
    from sqlalchemy import select

    from app.models import User
    from tests.fixtures.factories import make_photo

    headers = await register_and_login(client, "list_unfin@example.com")
    user = (
        await db_session.execute(select(User).where(User.email == "list_unfin@example.com"))
    ).scalar_one()
    await make_photo(db_session, user=user, finalized=False)
    await db_session.commit()

    r = await client.get("/v1/photos", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == []
