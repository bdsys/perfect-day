"""Test that hard_delete_user scrubs the tmp/ prefix."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_hard_delete_user_removes_tmp_prefix(
    db_session, db_url, s3_client, photos_bucket, monkeypatch, s3_endpoint
):
    import uuid

    import app.core.dependencies as deps
    from app.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_URL_SYNC", db_url.replace("+asyncpg", ""))
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    monkeypatch.setenv("S3_ENDPOINT_URL", s3_endpoint)
    get_settings.cache_clear()

    import app.core.database as db_module
    db_module._engine = None
    db_module._session_factory = None
    deps._s3_client = s3_client

    user_id = uuid.uuid4()
    tmp_key = f"tmp/{user_id}/leftover"
    s3_client.put_object(Bucket=photos_bucket, Key=tmp_key, Body=b"x")

    from app.workers.hard_delete import hard_delete_user
    await hard_delete_user(user_id)

    listed = s3_client.list_objects_v2(Bucket=photos_bucket, Prefix=f"tmp/{user_id}/").get(
        "Contents", []
    )
    assert listed == []
