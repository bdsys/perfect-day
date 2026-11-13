"""POST /v1/photos: photos go through the API and are encrypted before storage (S-7)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.photo_crypto import PURPOSE_FULL, PURPOSE_THUMB, iter_decrypt_stream, unwrap_dek
from app.models import Photo
from tests.integration.photo_helpers import (
    jpeg_with_exif,
    read_fixture,
    register_and_login,
    upload_photo,
)


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
    body = jpeg_with_exif()
    r = await client.post(
        "/v1/photos", content=body, headers={**headers, "Content-Type": "image/jpeg"}
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["has_thumbnail"] is True
    assert out["taken_at"].startswith("2025-08-14T13:45:00")
    assert out["lat"] == pytest.approx(37.7749, abs=1e-4)
    assert out["lon"] == pytest.approx(-122.4194, abs=1e-4)


@pytest.mark.asyncio
async def test_taken_at_fallback_used_when_no_exif_time(client, db_session):  # client wires S3
    from datetime import UTC, datetime

    from app.services.photo_ingest import ingest_photo
    from tests.fixtures.factories import make_user

    user = await make_user(db_session)
    fallback = datetime(2024, 3, 2, 10, 0, tzinfo=UTC)
    photo = await ingest_photo(
        db_session,
        owner=user,
        data=read_fixture("sample.jpg"),
        source="google_photos",
        external_id="g1",
        taken_at_fallback=fallback,
    )
    assert photo.taken_at == fallback
    assert photo.external_id == "g1" and photo.source == "google_photos"


class _Counting:
    """Async body that records how many chunks were pulled."""

    def __init__(self, n_chunks, size=400):
        self.n_chunks, self.size, self.pulled = n_chunks, size, 0

    async def __aiter__(self):
        for i in range(self.n_chunks):
            self.pulled += 1
            yield (b"\xff\xd8\xff" if i == 0 else b"") + b"0" * self.size


async def _count_rows(db_session):
    return (await db_session.execute(select(func.count()).select_from(Photo))).scalar_one()


@pytest.mark.asyncio
async def test_oversize_without_content_length_stops_reading(
    client, db_session, s3_client, photos_bucket, monkeypatch
):
    monkeypatch.setattr("app.routers.v1.photos.MAX_BYTES", 1000)
    headers = await register_and_login(client, "big@example.com")
    before = set(_objects(s3_client, photos_bucket))
    body = _Counting(n_chunks=200)
    r = await client.post(
        "/v1/photos", content=body, headers={**headers, "Content-Type": "image/jpeg"}
    )
    assert r.status_code == 413, r.text
    assert r.json()["error"]["message"] == "size_exceeds_limit"
    assert body.pulled < 200, "the body must not be read to the end"
    assert set(_objects(s3_client, photos_bucket)) == before
    assert await _count_rows(db_session) == 0


@pytest.mark.asyncio
async def test_oversize_content_length_rejected_before_reading(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.v1.photos.MAX_BYTES", 1000)
    headers = await register_and_login(client, "big2@example.com")
    body = _Counting(n_chunks=5)
    r = await client.post(
        "/v1/photos",
        content=body,
        headers={**headers, "Content-Type": "image/jpeg", "Content-Length": "2000"},
    )
    assert r.status_code == 413, r.text
    assert body.pulled <= 1
    assert await _count_rows(db_session) == 0


@pytest.mark.asyncio
async def test_unauthenticated_upload_401_without_reading_body(client):
    body = _Counting(n_chunks=50)
    r = await client.post("/v1/photos", content=body, headers={"Content-Type": "image/jpeg"})
    assert r.status_code in (401, 403)
    assert body.pulled < 50


@pytest.mark.asyncio
async def test_non_image_rejected_415(client, s3_client, photos_bucket):
    headers = await register_and_login(client, "pdf@example.com")
    before = set(_objects(s3_client, photos_bucket))
    r = await client.post(
        "/v1/photos",
        content=b"%PDF-1.4 not an image",
        headers={**headers, "Content-Type": "application/pdf"},
    )
    assert r.status_code == 415, r.text
    assert set(_objects(s3_client, photos_bucket)) == before


@pytest.mark.asyncio
async def test_upload_requires_auth(client):
    r = await client.post(
        "/v1/photos", content=read_fixture("sample.jpg"), headers={"Content-Type": "image/jpeg"}
    )
    assert r.status_code in (401, 403)
