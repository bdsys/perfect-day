"""POST /v1/photos: photos go through the API and are encrypted before storage (S-7)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.photo_crypto import PURPOSE_FULL, PURPOSE_THUMB, iter_decrypt_stream, unwrap_dek
from app.models import Photo
from tests.integration.photo_helpers import read_fixture, register_and_login, upload_photo


def _objects(s3_client, bucket):
    resp = s3_client.list_objects_v2(Bucket=bucket).get("Contents", [])
    return {
        o["Key"]: s3_client.get_object(Bucket=bucket, Key=o["Key"])["Body"].read() for o in resp
    }


def _decrypt(blob: bytes, dek: bytes, purpose) -> bytes:
    import io

    return b"".join(iter_decrypt_stream(io.BytesIO(blob).read, dek, purpose))


@pytest.mark.asyncio
async def test_upload_stores_only_ciphertext(client, db_session, s3_client, photos_bucket):
    headers = await register_and_login(client, "enc@example.com")
    body = read_fixture("sample.jpg")
    pid = await upload_photo(client, headers, body)

    objs = _objects(s3_client, photos_bucket)
    photo = (await db_session.execute(select(Photo).where(Photo.id == uuid.UUID(pid)))).scalar_one()
    mine = {k: v for k, v in objs.items() if k.startswith(f"{photo.user_id}/")}
    assert set(mine) == {photo.s3_key, photo.thumbnail_s3_key}
    assert not any(k.startswith("tmp/") for k in objs)
    assert all(body not in v for v in objs.values())
    dek = unwrap_dek(photo.dek_ciphertext, photo.user_id)
    assert _decrypt(objs[photo.s3_key], dek, PURPOSE_FULL) == body
    assert _decrypt(objs[photo.thumbnail_s3_key], dek, PURPOSE_THUMB)[:3] == b"\xff\xd8\xff"
    assert photo.finalized_at is not None and photo.source == "upload"


@pytest.mark.asyncio
async def test_upload_reads_exif_time_and_gps(client):
    headers = await register_and_login(client, "exif@example.com")
    body = read_fixture("sample.jpg")
    r = await client.post("/v1/photos", files={"file": ("a.jpg", body)}, headers=headers)
    assert r.status_code == 201, r.text
    out = r.json()
    from app.services.photos import parse_exif

    expected = parse_exif(body)
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["has_thumbnail"] is True
    assert out["taken_at"] is not None or expected["taken_at"] is None
    assert (out["lat"] is None) == (expected["lat"] is None)
    if expected["lat"] is not None:
        assert out["lat"] == pytest.approx(expected["lat"], abs=1e-5)
        assert out["lon"] == pytest.approx(expected["lon"], abs=1e-5)


@pytest.mark.asyncio
async def test_oversize_rejected_without_storing(
    client, db_session, s3_client, photos_bucket, monkeypatch
):
    monkeypatch.setattr("app.routers.v1.photos.MAX_BYTES", 1000)
    headers = await register_and_login(client, "big@example.com")
    before = set(_objects(s3_client, photos_bucket))
    r = await client.post(
        "/v1/photos", files={"file": ("a.jpg", b"\xff\xd8\xff" + b"0" * 998)}, headers=headers
    )
    assert r.status_code == 413, r.text
    assert r.json()["error"]["message"] == "size_exceeds_limit"
    assert set(_objects(s3_client, photos_bucket)) == before
    assert (await db_session.execute(select(func.count()).select_from(Photo))).scalar_one() == 0


@pytest.mark.asyncio
async def test_non_image_rejected_415(client, s3_client, photos_bucket):
    headers = await register_and_login(client, "pdf@example.com")
    before = set(_objects(s3_client, photos_bucket))
    r = await client.post(
        "/v1/photos", files={"file": ("a.pdf", b"%PDF-1.4 not an image")}, headers=headers
    )
    assert r.status_code == 415, r.text
    assert set(_objects(s3_client, photos_bucket)) == before


@pytest.mark.asyncio
async def test_upload_requires_auth(client):
    r = await client.post("/v1/photos", files={"file": ("a.jpg", read_fixture("sample.jpg"))})
    assert r.status_code in (401, 403)
