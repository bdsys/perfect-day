"""Integration tests for the orphaned photo sweeper."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.asyncio
async def test_sweep_removes_old_unfinalized_row(
    db_session, db_url, s3_client, photos_bucket, monkeypatch, s3_endpoint
):
    """Sweeper deletes Photo rows that are unfinalized after 24h."""
    import uuid

    import app.core.dependencies as deps
    from app.core.config import get_settings
    from app.models import Photo, User

    # Patch env so the sweeper's internal db_session and S3 hit the testcontainers
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_URL_SYNC", db_url.replace("+asyncpg", ""))
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    monkeypatch.setenv("S3_ENDPOINT_URL", s3_endpoint)
    get_settings.cache_clear()

    # Reset the cached engine/session factory so they pick up the new URL
    import app.core.database as db_module
    db_module._engine = None
    db_module._session_factory = None

    deps._s3_client = s3_client

    # Create a real user (required for FK constraint on photos.user_id)
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"{user_id}@sweeper-test.example.com",
        subscription_tier="free",
    )
    db_session.add(user)
    await db_session.flush()

    # Create old unfinalized photo row
    photo_id = uuid.uuid4()
    photo = Photo(
        id=photo_id,
        user_id=user_id,
        s3_key=f"{user_id}/{photo_id}.enc",
        source="upload",
        finalized_at=None,
    )
    db_session.add(photo)
    await db_session.commit()

    # Manually back-date created_at
    from sqlalchemy import update

    from app.models import Photo as PhotoModel
    await db_session.execute(
        update(PhotoModel)
        .where(PhotoModel.id == photo_id)
        .values(created_at=datetime.now(tz=UTC) - timedelta(hours=25))
    )
    await db_session.commit()

    # Put a stray tmp object in MinIO matching the key the sweeper will attempt to clean
    tmp_key = f"tmp/{user_id}/{photo_id}"
    s3_client.put_object(Bucket=photos_bucket, Key=tmp_key, Body=b"x")

    from app.workers.beat_tasks import _sweep_orphaned_photos
    deleted = await _sweep_orphaned_photos()

    assert deleted >= 1

    # Row should be gone (expire + re-query to force fresh read)
    db_session.expire_all()
    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(PhotoModel).where(PhotoModel.id == photo_id)
        )
    ).scalar_one_or_none()
    assert row is None

    # tmp object should be gone (best-effort)
    listed = s3_client.list_objects_v2(
        Bucket=photos_bucket, Prefix=f"tmp/{user_id}/"
    ).get("Contents", [])
    assert not any(o["Key"] == tmp_key for o in listed)
