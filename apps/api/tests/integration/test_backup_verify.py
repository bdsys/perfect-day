"""`photos verify`: restored photos and avatars decrypt with the restored keys (row 36 drill)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.photo_crypto import encrypt_stream, generate_dek, wrap_dek
from app.services.avatar import store_avatar
from app.services.backup_verify import (
    missing_referenced_keys,
    referenced_keys,
    verify_photo_sample,
)
from app.services.photos import put_object_bytes
from tests.fixtures.factories import make_photo, make_user

MANAGE = Path(__file__).resolve().parents[4] / "scripts" / "manage.py"


async def _photo(db: AsyncSession, user, body: bytes = b"jpeg bytes"):
    """A finalized photo whose object really is *body* encrypted under its own key."""
    dek = generate_dek()
    photo = await make_photo(db, user=user, dek_ciphertext=wrap_dek(dek, user.id))
    put_object_bytes(photo.s3_key, encrypt_stream(body, dek))
    return photo


async def _with_avatar(db: AsyncSession, user) -> None:
    key, wrapped = store_avatar(user.id, b"avatar jpeg")
    user.avatar_s3_key = key
    user.avatar_dek_ciphertext = wrapped
    await db.flush()


async def _two_photos_one_avatar(db: AsyncSession):
    user = await make_user(db)
    photos = [await _photo(db, user), await _photo(db, user)]
    await _with_avatar(db, user)
    await db.commit()
    return user, photos


async def test_all_good(client, db_session, sync_engine) -> None:
    await _two_photos_one_avatar(db_session)

    with Session(sync_engine) as db:
        report = verify_photo_sample(db)

    assert report.checked == 3
    assert report.failures == []


async def test_corrupted_object_reported(client, db_session, sync_engine) -> None:
    _, photos = await _two_photos_one_avatar(db_session)
    put_object_bytes(photos[0].s3_key, b"not what was stored")

    with Session(sync_engine) as db:
        report = verify_photo_sample(db)

    assert report.checked == 3
    assert len(report.failures) == 1
    assert str(photos[0].id) in report.failures[0]
    assert report.failures[0].startswith("photo ")


async def test_missing_object_reported(client, db_session, sync_engine) -> None:
    user = await make_user(db_session)
    photo = await make_photo(db_session, user=user)  # row only: its object was never stored
    await db_session.commit()

    with Session(sync_engine) as db:
        report = verify_photo_sample(db)

    assert report.checked == 1
    assert [f for f in report.failures if str(photo.id) in f]


async def test_wrong_master_secret_fails_all(client, db_session, sync_engine, monkeypatch) -> None:
    await _two_photos_one_avatar(db_session)
    from app.core.config import get_settings

    monkeypatch.setenv("MASTER_SECRET", "ff" * 32)
    get_settings.cache_clear()
    try:
        with Session(sync_engine) as db:
            report = verify_photo_sample(db)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert report.checked == 3
    assert len(report.failures) == 3


async def test_limit_respected(client, db_session, sync_engine) -> None:
    user = await make_user(db_session)
    for _ in range(25):
        await _photo(db_session, user)
    await _with_avatar(db_session, user)
    await db_session.commit()

    with Session(sync_engine) as db:
        report = verify_photo_sample(db, limit=20)

    assert report.checked == 21  # 20 photos + the avatar
    assert report.failures == []


@pytest.fixture
def manage(sync_engine, monkeypatch):
    spec = importlib.util.spec_from_file_location("manage_cli_verify", MANAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["manage_cli_verify"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_make_session", lambda: Session(sync_engine))
    return module


async def test_cli_exit_codes(client, db_session, manage) -> None:
    _, photos = await _two_photos_one_avatar(db_session)
    runner = CliRunner()

    good = runner.invoke(manage.cli, ["photos", "verify"])
    assert good.exit_code == 0, good.output
    assert "checked=3 failures=0" in good.output

    put_object_bytes(photos[1].s3_key, b"garbage")
    bad = runner.invoke(manage.cli, ["photos", "verify"])
    assert bad.exit_code == 1
    assert "checked=3 failures=1" in bad.output
    assert str(photos[1].id) in bad.output


async def test_cli_nothing_checked_with_rows_present_fails(client, db_session, manage) -> None:
    # Rows exist but none qualifies (unfinalized), so nothing could be checked: not a pass.
    user = await make_user(db_session)
    await make_photo(db_session, user=user, finalized=False)
    await db_session.commit()

    result = CliRunner().invoke(manage.cli, ["photos", "verify"])

    assert result.exit_code == 1
    assert "checked=0" in result.output


# ── Every referenced object present (row 41: the storage switch refuses otherwise) ──


async def test_missing_referenced_keys_lists_absent_photo_and_avatar_keys(
    client, db_session, sync_engine, s3_client
) -> None:
    user = await make_user(db_session)
    whole = await _photo(db_session, user)
    put_object_bytes(whole.thumbnail_s3_key, b"thumb")
    whole.display_s3_key = f"{user.id}/{whole.id}_display.enc"  # never stored
    user.avatar_s3_key = f"{user.id}/avatar/{whole.id.hex}.jpg"  # never stored
    gone = await make_photo(db_session, user=user)  # soft-deleted: its objects may be gone
    gone.deleted_at = datetime.now(tz=UTC)
    await make_photo(db_session, user=user, finalized=False)  # upload not finished
    await db_session.commit()

    with Session(sync_engine) as db:
        referenced = referenced_keys(db)
        missing = missing_referenced_keys(db, s3_client, "photos-test")

    assert missing == sorted([whole.display_s3_key, user.avatar_s3_key])
    needed = {whole.s3_key, whole.thumbnail_s3_key, whole.display_s3_key, user.avatar_s3_key}
    assert needed <= referenced
    assert gone.s3_key not in referenced


async def test_missing_referenced_keys_empty_when_all_present(
    client, db_session, sync_engine, s3_client
) -> None:
    user, photos = await _two_photos_one_avatar(db_session)
    for photo in photos:
        put_object_bytes(photo.thumbnail_s3_key, b"thumb")

    with Session(sync_engine) as db:
        assert missing_referenced_keys(db, s3_client, "photos-test") == []


async def test_cli_photos_missing_exits_1_and_never_prints_full_keys(
    client, db_session, manage
) -> None:
    user, photos = await _two_photos_one_avatar(db_session)
    for photo in photos:
        put_object_bytes(photo.thumbnail_s3_key, b"thumb")
    runner = CliRunner()

    good = runner.invoke(manage.cli, ["photos", "missing"])
    assert good.exit_code == 0, good.output
    assert good.output.splitlines()[0] == "missing=0"

    photos[0].display_s3_key = f"{user.id}/{photos[0].id}_display.enc"
    await db_session.commit()
    bad = runner.invoke(manage.cli, ["photos", "missing"])
    assert bad.exit_code == 1
    assert bad.output.splitlines()[0] == "missing=1"
    assert photos[0].display_s3_key not in bad.output
    assert str(user.id) not in bad.output
