"""HEIC photos get a stored, encrypted JPEG copy for full-size viewing (item 14)."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import Photo
from tests.fixtures.factories import make_diary, make_entry, make_entry_photo, make_photo, make_user
from tests.integration.photo_helpers import (
    gps_heic_bytes,
    gps_of,
    heic_bytes,
    read_fixture,
    register_and_login,
    upload_photo,
)


async def _row(db_session, photo_id: str) -> Photo:
    db_session.expire_all()
    return (
        await db_session.execute(select(Photo).where(Photo.id == photo_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_heic_upload_full_is_jpeg(client, db_session):
    h = await register_and_login(client, "heic1@example.com")
    body = gps_heic_bytes()
    assert gps_of(body)  # the fixture really carries GPS
    pid = await upload_photo(client, h, body, "image/heic")
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "JPEG"
    assert not gps_of(r.content)
    assert b"GPSLatitude" not in r.content
    assert (await _row(db_session, pid)).display_s3_key is not None


@pytest.mark.asyncio
async def test_heic_original_still_available(client):
    h = await register_and_login(client, "heic2@example.com")
    data = heic_bytes()
    pid = await upload_photo(client, h, data, "image/heic")
    r = await client.get(f"/v1/photos/{pid}?kind=original", headers=h)
    assert r.status_code == 200
    assert r.content == data
    assert r.headers["content-type"].startswith("image/heic")


@pytest.mark.asyncio
async def test_jpeg_upload_has_no_display_copy(client, db_session):
    h = await register_and_login(client, "heic3@example.com")
    data = read_fixture("sample.jpg")
    pid = await upload_photo(client, h, data)
    assert (await _row(db_session, pid)).display_s3_key is None
    r = await client.get(f"/v1/photos/{pid}?kind=full", headers=h)
    assert r.content == data


def test_make_display_jpeg_none_for_browser_types():
    from app.services.photos import make_display_jpeg

    assert make_display_jpeg(read_fixture("sample.jpg"), "image/jpeg") is None
    assert make_display_jpeg(heic_bytes(), "image/heic")[:3] == b"\xff\xd8\xff"


async def _old_heic(db_session, s3_client, photos_bucket, user):
    from app.core.photo_crypto import encrypt_stream, generate_dek, wrap_dek

    dek = generate_dek()
    photo = await make_photo(
        db_session, user=user, mime_type="image/heic", dek_ciphertext=wrap_dek(dek, user.id)
    )
    await db_session.commit()
    s3_client.put_object(
        Bucket=photos_bucket, Key=photo.s3_key, Body=encrypt_stream(heic_bytes(), dek)
    )
    return photo


@pytest.mark.asyncio
async def test_backfill_adds_display_copy_to_old_heic(
    client, db_session, s3_client, photos_bucket
):
    from app.workers.beat_tasks import create_missing_display_copies

    user = await make_user(db_session)
    photo = await _old_heic(db_session, s3_client, photos_bucket, user)
    # a broken one must not stop the good one
    bad = await make_photo(db_session, user=user, mime_type="image/heic")
    await db_session.commit()
    user_id, photo_id, bad_id = user.id, str(photo.id), str(bad.id)

    assert await create_missing_display_copies() == 1

    expected = f"{user_id}/{photo_id}_display.enc"
    assert (await _row(db_session, photo_id)).display_s3_key == expected
    assert (await _row(db_session, bad_id)).display_s3_key is None
    keys = {o["Key"] for o in s3_client.list_objects_v2(Bucket=photos_bucket)["Contents"]}
    assert expected in keys


@pytest.mark.asyncio
async def test_hard_delete_removes_display_object(
    client, db_session, s3_client, photos_bucket
):
    from app.workers.hard_delete import hard_delete_diary

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    entry = await make_entry(db_session, diary)
    photo = await make_photo(db_session, user=user, mime_type="image/heic")
    photo.display_s3_key = f"{user.id}/{photo.id}_display.enc"
    await make_entry_photo(db_session, entry=entry, photo=photo)
    await db_session.commit()
    s3_client.put_object(Bucket=photos_bucket, Key=photo.display_s3_key, Body=b"d")

    await hard_delete_diary(diary.id)

    keys = {
        o["Key"] for o in s3_client.list_objects_v2(Bucket=photos_bucket).get("Contents", [])
    }
    assert photo.display_s3_key not in keys


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["original", "full"])
@pytest.mark.parametrize("backfilled", [False, True])
async def test_coparent_heic_original_has_no_gps(client, db_session, kind, backfilled):
    from tests.integration.test_shared_photos import _setup

    body = gps_heic_bytes()
    auth, pid, _ = await _setup(
        client, db_session, f"hg-{kind}-{int(backfilled)}", False, body=body
    )
    if not backfilled:  # no display copy yet: full falls back to the stripped original
        row = await _row(db_session, pid)
        row.display_s3_key = None
        await db_session.commit()
    r = await client.get(f"/v1/photos/{pid}?kind={kind}", headers=auth["owner"])
    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).size == (32, 24)
    assert not gps_of(r.content)
    assert b"GPSLatitude" not in r.content


@pytest.mark.asyncio
async def test_uploader_heic_original_keeps_gps(client):
    h = await register_and_login(client, "heic-up@example.com")
    body = gps_heic_bytes()
    pid = await upload_photo(client, h, body, "image/heic")
    r = await client.get(f"/v1/photos/{pid}?kind=original", headers=h)
    assert r.content == body
    assert gps_of(r.content)


def test_strip_location_writes_no_xmp_gps_for_jpeg_or_png():
    from app.services.photos import strip_location

    xmp = b"<x:xmpmeta><exif:GPSLatitude>47,36N</exif:GPSLatitude></x:xmpmeta>"
    jpg = io.BytesIO()
    Image.new("RGB", (8, 8)).save(jpg, "JPEG", xmp=xmp)
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG", exif=gps_heic_exif())
    assert b"GPSLatitude" in jpg.getvalue()
    for data, mime in ((jpg.getvalue(), "image/jpeg"), (png.getvalue(), "image/png")):
        out = strip_location(data, mime)
        assert b"GPSLatitude" not in out
        assert not gps_of(out)


def gps_heic_exif() -> bytes:
    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    return exif.tobytes()


@pytest.mark.asyncio
async def test_bad_photos_do_not_starve_new_ones(client, db_session, s3_client, photos_bucket):
    from datetime import UTC, datetime, timedelta

    from app.workers.beat_tasks import create_missing_display_copies

    user = await make_user(db_session)
    old = datetime.now(tz=UTC) - timedelta(days=30)
    bad_ids = []
    for i in range(20):  # objects missing: permanently bad, and older than the good one
        bad = await make_photo(db_session, user=user, mime_type="image/heic")
        bad.created_at = old + timedelta(minutes=i)
        bad_ids.append(str(bad.id))
    await db_session.commit()
    good = await _old_heic(db_session, s3_client, photos_bucket, user)
    good_id = str(good.id)

    await create_missing_display_copies(limit=20)
    await create_missing_display_copies(limit=20)

    db_session.expire_all()
    assert (await _row(db_session, good_id)).display_s3_key is not None
    for bid in bad_ids:
        assert (await _row(db_session, bid)).display_attempted_at is not None


@pytest.mark.asyncio
async def test_ingest_logs_display_failure(client, db_session):
    from unittest.mock import patch

    from structlog.testing import capture_logs

    from app.services.photo_ingest import ingest_photo

    user = await make_user(db_session)
    with (
        patch("app.services.photo_ingest.make_display_jpeg", side_effect=ValueError("secret-text")),
        capture_logs() as logs,
    ):
        photo = await ingest_photo(
            db_session, owner=user, data=heic_bytes(), source="upload"
        )
    assert photo.display_s3_key is None
    events = [e for e in logs if e["event"] == "display_copy_failed"]
    assert len(events) == 1
    assert events[0]["photo_id"] is not None
    assert events[0]["error_class"] == "ValueError"
    assert set(events[0]) <= {"event", "log_level", "photo_id", "error_class"}
    assert "secret-text" not in str(events[0])
