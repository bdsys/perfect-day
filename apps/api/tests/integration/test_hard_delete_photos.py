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


@pytest.mark.asyncio
async def test_hard_delete_removes_avatar(client, db_session, s3_client, photos_bucket):
    from app.services.avatar import make_avatar_jpeg, store_avatar
    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_user, mark_due_for_hard_delete
    from tests.integration.photo_helpers import jpeg_with_exif

    user = await make_user(db_session)
    key, wrapped = store_avatar(user.id, make_avatar_jpeg(jpeg_with_exif()))
    user.avatar_s3_key, user.avatar_dek_ciphertext = key, wrapped
    await db_session.commit()
    user_id = user.id
    listed = s3_client.list_objects_v2(Bucket=photos_bucket, Prefix=f"{user_id}/avatar/")
    assert [o["Key"] for o in listed.get("Contents", [])] == [key]
    await mark_due_for_hard_delete(db_session, user)

    await hard_delete_user(user_id)

    listed = s3_client.list_objects_v2(Bucket=photos_bucket, Prefix=f"{user_id}/")
    assert listed.get("Contents", []) == []


# ---------------------------------------------------------------------------
# 2026-11-20: a storage error stops the delete (the hourly job retries it), and a
# hard delete revokes any Google grant still held.
# ---------------------------------------------------------------------------


def _storage_down():
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "down"}}, "Delete")


@pytest.mark.asyncio
async def test_user_scrub_failure_keeps_user_for_retry(
    client, db_session, s3_client, photos_bucket
):
    from unittest.mock import patch

    from botocore.exceptions import ClientError
    from sqlalchemy import select

    from app.core.dependencies import get_s3
    from app.models import User
    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_user, mark_due_for_hard_delete

    user = await make_user(db_session)
    await mark_due_for_hard_delete(db_session, user)
    user_id = user.id
    key = f"{user_id}/avatar/a.enc"
    s3_client.put_object(Bucket=photos_bucket, Key=key, Body=b"x")

    with patch.object(get_s3(), "delete_objects", side_effect=_storage_down()):
        with pytest.raises(ClientError):
            await hard_delete_user(user_id)

    db_session.expire_all()
    assert (await db_session.execute(select(User.id).where(User.id == user_id))).first() is not None

    await hard_delete_user(user_id)  # storage is back: the next run finishes it
    db_session.expire_all()
    assert (await db_session.execute(select(User.id).where(User.id == user_id))).first() is None
    listed = s3_client.list_objects_v2(Bucket=photos_bucket, Prefix=f"{user_id}/")
    assert listed.get("Contents", []) == []


@pytest.mark.asyncio
async def test_diary_photo_delete_failure_keeps_rows(
    client, db_session, s3_client, photos_bucket
):
    from unittest.mock import patch

    from botocore.exceptions import ClientError
    from sqlalchemy import select

    from app.core.dependencies import get_s3
    from app.models import Diary, Photo
    from app.workers.hard_delete import hard_delete_diary
    from tests.fixtures.factories import (
        make_diary,
        make_entry,
        make_entry_photo,
        make_photo,
        make_user,
    )

    user = await make_user(db_session)
    diary = await make_diary(db_session, user)
    entry = await make_entry(db_session, diary)
    photo = await make_photo(db_session, user=user)
    await make_entry_photo(db_session, entry=entry, photo=photo)
    await db_session.commit()
    diary_id, photo_id = diary.id, photo.id

    with patch.object(get_s3(), "delete_object", side_effect=_storage_down()):
        with pytest.raises(ClientError):
            await hard_delete_diary(diary_id)

    db_session.expire_all()
    photo_row = (await db_session.execute(select(Photo.id).where(Photo.id == photo_id))).first()
    diary_row = (await db_session.execute(select(Diary.id).where(Diary.id == diary_id))).first()
    assert photo_row is not None
    assert diary_row is not None


@pytest.mark.asyncio
async def test_hard_delete_user_revokes_google(client, db_session, photos_bucket):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, patch

    import httpx
    from sqlalchemy import select

    from app.core.security import encrypt_oauth_token
    from app.models import OAuthToken
    from app.services.google_revoke import GOOGLE_REVOKE_URL
    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_user, mark_due_for_hard_delete

    user = await make_user(db_session)
    await mark_due_for_hard_delete(db_session, user)
    user_id = user.id
    db_session.add(
        OAuthToken(
            user_id=user_id,
            provider="google",
            access_token_ciphertext=encrypt_oauth_token("access-1"),
            refresh_token_ciphertext=encrypt_oauth_token("refresh-1"),
            scopes_granted=["calendar.readonly"],
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    post = AsyncMock(return_value=httpx.Response(200, json={}))
    with patch("httpx.AsyncClient.post", post):
        await hard_delete_user(user_id)

    revokes = [c for c in post.await_args_list if c.args and c.args[0] == GOOGLE_REVOKE_URL]
    assert len(revokes) == 1
    assert revokes[0].kwargs["data"] == {"token": "refresh-1"}
    db_session.expire_all()
    rows = (await db_session.execute(select(OAuthToken).where(OAuthToken.user_id == user_id))).all()
    assert rows == []
