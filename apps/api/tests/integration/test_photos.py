"""Integration tests for photo upload, encryption, and attachment."""
from __future__ import annotations

import pytest


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
        delete_object, get_object_bytes, head_object_size, put_object_bytes,
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


@pytest.mark.asyncio
async def test_presign_put_url_round_trip(s3_client, photos_bucket, monkeypatch):
    import httpx
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import presign_put_url

    body = b"hello-presigned-world"
    url = presign_put_url("tmp/test/abc", "image/jpeg", len(body))
    assert "Signature=" in url or "X-Amz-Signature" in url
    resp = httpx.put(
        url,
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    )
    assert resp.status_code == 200
    obj = s3_client.get_object(Bucket=photos_bucket, Key="tmp/test/abc")
    assert obj["Body"].read() == body


@pytest.mark.asyncio
async def test_photos_router_registered(client):
    """The photos router should be mounted; /v1/photos/upload-url returns 401 without auth."""
    resp = await client.post("/v1/photos/upload-url", json={"declared_mime": "image/jpeg", "declared_size": 100})
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Task 10: POST /v1/photos/upload-url
# ---------------------------------------------------------------------------


async def _login(client, email="user@example.com"):
    """Helper: register and return Authorization header."""
    await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    r = await client.post("/v1/auth/login", json={"email": email, "password": "Password1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upload_url_happy_path(client):
    headers = await _login(client)
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": 12345},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "upload_url" in body
    assert body["upload_key"].startswith("tmp/")
    assert body["expires_in"] == 900
    assert body["required_headers"]["Content-Type"] == "image/jpeg"
    assert body["required_headers"]["Content-Length"] == "12345"


@pytest.mark.asyncio
async def test_upload_url_rejects_non_whitelisted_mime(client):
    headers = await _login(client, "u2@example.com")
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "application/pdf", "declared_size": 100},
        headers=headers,
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_upload_url_rejects_oversize(client):
    headers = await _login(client, "u3@example.com")
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": 60 * 1024 * 1024},
        headers=headers,
    )
    assert r.status_code == 413


# ---------------------------------------------------------------------------
# Task 11: POST /v1/photos/{id}/finalize
# ---------------------------------------------------------------------------


def _read_fixture(name: str) -> bytes:
    import pathlib
    return (pathlib.Path(__file__).parent.parent / "fixtures" / "photos" / name).read_bytes()


@pytest.mark.asyncio
async def test_finalize_happy_path(client, s3_client, photos_bucket):
    import httpx
    headers = await _login(client, "fin@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    assert r1.status_code == 201
    payload = r1.json()
    photo_id = payload["photo_id"]

    httpx.put(
        payload["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()

    r2 = await client.post(f"/v1/photos/{photo_id}/finalize", headers=headers)
    assert r2.status_code == 200, r2.text
    out = r2.json()
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["finalized_at"] is not None
    assert out["has_thumbnail"] is True

    # tmp object gone, .enc and _thumb.enc present
    objs = s3_client.list_objects_v2(Bucket=photos_bucket).get("Contents", [])
    keys = {o["Key"] for o in objs}
    assert any(k.endswith(f"{photo_id}.enc") for k in keys)
    assert any(k.endswith(f"{photo_id}_thumb.enc") for k in keys)
    assert not any(k.startswith("tmp/") and photo_id in k for k in keys)


@pytest.mark.asyncio
async def test_finalize_idempotent_returns_200(client):
    import httpx
    headers = await _login(client, "fin2@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    assert (await client.post(f"/v1/photos/{pid}/finalize", headers=headers)).status_code == 200
    r3 = await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    assert r3.status_code == 200  # idempotent — already finalized is OK


@pytest.mark.asyncio
async def test_finalize_rejects_wrong_user(client):
    import httpx
    headers_a = await _login(client, "fina@example.com")
    headers_b = await _login(client, "finb@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers_a,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    r2 = await client.post(f"/v1/photos/{pid}/finalize", headers=headers_b)
    assert r2.status_code == 404  # not visible to other user


@pytest.mark.asyncio
async def test_finalize_rejects_missing_tmp(client):
    import httpx  # noqa: F401
    headers = await _login(client, "fin3@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    # Do NOT upload the file — just call finalize
    r2 = await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    assert r2.status_code == 422  # tmp object not found


# ---------------------------------------------------------------------------
# Task 12: GET /v1/photos/{id}?kind=full|thumb
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_photo_full_round_trip(client, s3_client, photos_bucket):
    import httpx
    headers = await _login(client, "get1@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r2 = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/jpeg")
    assert r2.content == body  # round-trip lossless for JPEG


@pytest.mark.asyncio
async def test_get_photo_thumb(client):
    import httpx
    headers = await _login(client, "get2@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r2 = await client.get(f"/v1/photos/{pid}?kind=thumb", headers=headers)
    assert r2.status_code == 200
    assert r2.content[:3] == b"\xff\xd8\xff"  # thumbnail is JPEG


@pytest.mark.asyncio
async def test_get_photo_wrong_user_returns_404(client):
    import httpx
    headers_a = await _login(client, "ga@example.com")
    headers_b = await _login(client, "gb@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers_a,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers_a)

    r2 = await client.get(f"/v1/photos/{pid}", headers=headers_b)
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Task 13: DELETE /v1/photos/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_photo_soft_deletes(client):
    import httpx
    headers = await _login(client, "del1@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r2 = await client.delete(f"/v1/photos/{pid}", headers=headers)
    assert r2.status_code == 204

    # Can no longer fetch
    r3 = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_delete_photo_wrong_user_returns_404(client):
    import httpx
    headers_a = await _login(client, "dela@example.com")
    headers_b = await _login(client, "delb@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers_a,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers_a)

    r2 = await client.delete(f"/v1/photos/{pid}", headers=headers_b)
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Task 14: Diary photo attach/detach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_detach_diary_photo(client):
    import httpx
    headers = await _login(client, "diary1@example.com")
    body = _read_fixture("sample.jpg")

    # Create diary
    r_diary = await client.post(
        "/v1/diaries",
        json={"name": "Test Diary", "timezone": "America/New_York", "scan_interval_minutes": 60},
        headers=headers,
    )
    diary_id = r_diary.json()["id"]

    # Upload + finalize photo
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    # Attach
    r2 = await client.post(
        f"/v1/diaries/{diary_id}/photos",
        json={"photo_id": pid},
        headers=headers,
    )
    assert r2.status_code == 201, r2.text

    # Detach
    r3 = await client.delete(f"/v1/diaries/{diary_id}/photos/{pid}", headers=headers)
    assert r3.status_code == 204


@pytest.mark.asyncio
async def test_attach_diary_photo_wrong_owner_returns_403(client):
    import httpx
    headers_a = await _login(client, "doa@example.com")
    headers_b = await _login(client, "dob@example.com")
    body = _read_fixture("sample.jpg")

    r_diary = await client.post(
        "/v1/diaries",
        json={"name": "TD2", "timezone": "America/New_York", "scan_interval_minutes": 60},
        headers=headers_a,
    )
    diary_id = r_diary.json()["id"]

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers_b,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers_b)

    r2 = await client.post(
        f"/v1/diaries/{diary_id}/photos",
        json={"photo_id": str(pid)},
        headers=headers_b,
    )
    assert r2.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Task 15: Entry photo attach/detach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_detach_entry_photo(client):
    import httpx
    headers = await _login(client, "ent1@example.com")
    body = _read_fixture("sample.jpg")

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
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

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
    import httpx
    headers_owner = await _login(client, "eown@example.com")
    headers_viewer = await _login(client, "eview@example.com")
    body = _read_fixture("sample.jpg")

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

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers_viewer,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers_viewer)

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
    import httpx
    headers = await _login(client, "meta1@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
              headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r2 = await client.get(f"/v1/photos/{pid}/metadata", headers=headers)
    assert r2.status_code == 200
    out = r2.json()
    assert out["id"] == pid
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["has_thumbnail"] is True
