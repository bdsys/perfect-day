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
