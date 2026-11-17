"""Profile photo: stored like entry photos (no metadata, encrypted), served to its owner only."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.integration.photo_helpers import (
    gps_heic_bytes,
    gps_of,
    jpeg_with_exif,
    register_and_login,
)

ME = "/v1/auth/me"
AVATAR = "/v1/auth/me/avatar"


async def _put(client: AsyncClient, headers: dict[str, str], body: bytes):
    return await client.put(AVATAR, content=body, headers={**headers, "Content-Type": "image/jpeg"})


async def _row(db_session: AsyncSession, user_id: str) -> User:
    db_session.expire_all()
    return (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()


def _keys(s3_client, bucket: str, prefix: str) -> set[str]:
    listed = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    return {o["Key"] for o in listed}


async def test_upload_stores_encrypted_256_square(
    client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
) -> None:
    h = await register_and_login(client, "avatar-up@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]

    r = await _put(client, h, jpeg_with_exif())
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] == {"kind": "photo"}

    row = await _row(db_session, user_id)
    assert row.avatar_s3_key is not None
    assert row.avatar_s3_key.startswith(f"{user_id}/avatar/")
    assert row.avatar_s3_key.endswith(".jpg")
    assert row.avatar_dek_ciphertext is not None
    assert row.avatar_icon is None
    raw = s3_client.get_object(Bucket=photos_bucket, Key=row.avatar_s3_key)["Body"].read()
    assert raw[:4] == b"PD02"

    g = await client.get(AVATAR, headers=h)
    assert g.status_code == 200
    assert g.headers["content-type"] == "image/jpeg"
    assert g.headers["cache-control"] == "private, no-store"
    img = Image.open(io.BytesIO(g.content))
    assert img.format == "JPEG"
    assert img.size == (256, 256)


async def test_served_copy_has_no_gps(client: AsyncClient) -> None:
    h = await register_and_login(client, "avatar-gps@example.com")
    body = jpeg_with_exif(lat=47.6062, lon=-122.3321)
    assert gps_of(body)  # the upload really carries GPS
    assert (await _put(client, h, body)).status_code == 200

    g = await client.get(AVATAR, headers=h)
    assert gps_of(g.content) == {}
    assert len(Image.open(io.BytesIO(g.content)).getexif()) == 0  # no metadata at all


async def test_heic_upload_works(client: AsyncClient) -> None:
    h = await register_and_login(client, "avatar-heic@example.com")
    body = gps_heic_bytes()
    assert gps_of(body)
    r = await client.put(AVATAR, content=body, headers={**h, "Content-Type": "image/heic"})
    assert r.status_code == 200, r.text

    g = await client.get(AVATAR, headers=h)
    img = Image.open(io.BytesIO(g.content))
    assert img.format == "JPEG"
    assert img.size == (256, 256)
    assert gps_of(g.content) == {}


async def test_exif_orientation_applied_and_centre_cropped(client: AsyncClient) -> None:
    # 40 x 20: left half red, right half blue, tagged "rotate 90° clockwise". Upright it is
    # 20 x 40 with red on top; the centre square is then red over blue.
    src = Image.new("RGB", (40, 20), (255, 0, 0))
    src.paste((0, 0, 255), (20, 0, 40, 20))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    src.save(buf, format="JPEG", quality=95, exif=exif.tobytes())

    h = await register_and_login(client, "avatar-rotate@example.com")
    assert (await _put(client, h, buf.getvalue())).status_code == 200
    img = Image.open(io.BytesIO((await client.get(AVATAR, headers=h)).content)).convert("RGB")
    top_right = img.getpixel((200, 30))
    bottom_left = img.getpixel((64, 226))
    assert top_right[0] > 200 and top_right[2] < 60, top_right  # red
    assert bottom_left[2] > 200 and bottom_left[0] < 60, bottom_left  # blue


@pytest.mark.parametrize("body", [b"not an image", b"\xff\xd8\xff\xe0" + b"\x00" * 40])
async def test_bad_upload_is_415(
    client: AsyncClient, db_session: AsyncSession, body: bytes
) -> None:
    h = await register_and_login(client, f"avatar-bad-{len(body)}@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200
    before = (await _row(db_session, user_id)).avatar_s3_key

    r = await _put(client, h, body)
    assert r.status_code == 415
    assert r.json()["error"]["message"] == "unsupported_mime"
    assert (await _row(db_session, user_id)).avatar_s3_key == before
    assert (await client.get(ME, headers=h)).json()["avatar"] == {"kind": "photo"}


async def test_oversize_upload_is_413(client: AsyncClient, monkeypatch) -> None:
    import app.routers.v1.photos as photos_router

    monkeypatch.setattr(photos_router, "MAX_BYTES", 10)
    h = await register_and_login(client, "avatar-big@example.com")
    r = await _put(client, h, jpeg_with_exif())
    assert r.status_code == 413
    assert r.json()["error"]["message"] == "size_exceeds_limit"


async def test_replace_deletes_previous_object(
    client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
) -> None:
    h = await register_and_login(client, "avatar-replace@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200
    first = (await _row(db_session, user_id)).avatar_s3_key

    assert (await _put(client, h, jpeg_with_exif())).status_code == 200
    second = (await _row(db_session, user_id)).avatar_s3_key
    assert second != first
    assert _keys(s3_client, photos_bucket, f"{user_id}/avatar/") == {second}


async def test_choosing_icon_deletes_photo_object(
    client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
) -> None:
    h = await register_and_login(client, "avatar-icon@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200

    r = await client.patch(ME, json={"avatar_icon": "cat"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] == {"kind": "icon", "name": "cat"}
    assert _keys(s3_client, photos_bucket, f"{user_id}/avatar/") == set()
    row = await _row(db_session, user_id)
    assert row.avatar_s3_key is None and row.avatar_dek_ciphertext is None
    assert (await client.get(AVATAR, headers=h)).status_code == 404


async def test_delete_avatar(
    client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
) -> None:
    h = await register_and_login(client, "avatar-del@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200

    r = await client.delete(AVATAR, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] == {"kind": "initial"}
    assert _keys(s3_client, photos_bucket, f"{user_id}/avatar/") == set()
    row = await _row(db_session, user_id)
    assert row.avatar_s3_key is None and row.avatar_dek_ciphertext is None
    assert (await client.get(AVATAR, headers=h)).status_code == 404


async def test_old_object_delete_failure_still_saves(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    import app.services.avatar as avatar_service

    h = await register_and_login(client, "avatar-delfail@example.com")
    user_id = (await client.get(ME, headers=h)).json()["id"]
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200
    first = (await _row(db_session, user_id)).avatar_s3_key

    def boom(key: str) -> None:
        raise RuntimeError("storage down")

    monkeypatch.setattr(avatar_service, "delete_object", boom)
    r = await _put(client, h, jpeg_with_exif())
    assert r.status_code == 200, r.text
    second = (await _row(db_session, user_id)).avatar_s3_key
    assert second is not None and second != first
    assert (await client.get(AVATAR, headers=h)).status_code == 200


async def test_avatar_is_owner_only(client: AsyncClient) -> None:
    a = await register_and_login(client, "avatar-owner-a@example.com")
    b = await register_and_login(client, "avatar-owner-b@example.com")
    assert (await _put(client, a, jpeg_with_exif())).status_code == 200

    r = await client.get(AVATAR, headers=b)
    assert r.status_code == 404
    assert (await client.get(AVATAR)).status_code == 401


async def test_get_without_avatar_is_404(client: AsyncClient) -> None:
    h = await register_and_login(client, "avatar-none@example.com")
    r = await client.get(AVATAR, headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "not_found"


async def test_avatar_routes_need_active_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    h = await register_and_login(client, "avatar-pending@example.com")
    assert (await _put(client, h, jpeg_with_exif())).status_code == 200
    now = datetime.now(tz=UTC)
    await db_session.execute(
        update(User)
        .where(User.email == "avatar-pending@example.com")
        .values(deleted_at=now, hard_delete_after=now + timedelta(days=7))
    )
    await db_session.commit()
    for r in (
        await client.get(AVATAR, headers=h),
        await _put(client, h, jpeg_with_exif()),
        await client.delete(AVATAR, headers=h),
    ):
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "account_pending_deletion"


def test_decompression_bomb_is_unsupported(monkeypatch) -> None:
    from app.services.avatar import make_avatar_jpeg
    from app.services.photo_ingest import UnsupportedPhoto

    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # 4096 px is > 2x: an error, not a warning
    with pytest.raises(UnsupportedPhoto):
        make_avatar_jpeg(buf.getvalue())
