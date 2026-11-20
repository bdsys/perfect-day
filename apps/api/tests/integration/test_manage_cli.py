"""`manage.py users set-tier`: moves a user between plans and rejects old tier names."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy.orm import Session

from app.models import User

MANAGE = Path(__file__).resolve().parents[4] / "scripts" / "manage.py"


@pytest.fixture
def manage(sync_engine, monkeypatch):
    spec = importlib.util.spec_from_file_location("manage_cli", MANAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["manage_cli"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_make_session", lambda: Session(sync_engine))
    return module


def _make_user(sync_engine, email: str) -> None:
    with Session(sync_engine) as db:
        db.add(User(id=uuid.uuid4(), email=email))
        db.commit()


def _tier(sync_engine, email: str) -> str:
    with sync_engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT subscription_tier FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()


def test_set_tier_plus_updates_the_row(manage, sync_engine) -> None:
    _make_user(sync_engine, "cli-plus@example.com")
    result = CliRunner().invoke(
        manage.cli, ["users", "set-tier", "--email", "cli-plus@example.com", "--tier", "plus"]
    )
    assert result.exit_code == 0, result.output
    assert "free → plus" in result.output
    assert _tier(sync_engine, "cli-plus@example.com") == "plus"


def test_set_tier_rejects_old_tier_name(manage, sync_engine) -> None:
    _make_user(sync_engine, "cli-old@example.com")
    result = CliRunner().invoke(
        manage.cli, ["users", "set-tier", "--email", "cli-old@example.com", "--tier", "tier1"]
    )
    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert _tier(sync_engine, "cli-old@example.com") == "free"


def test_database_url_comes_from_the_environment_first(manage, monkeypatch) -> None:
    # On the NUC, admin.sh runs inside the API container, where the URL is in the
    # environment (from app.env) and there is no apps/api/.env next to manage.py.
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql://u:p@postgres:5432/perfectday")
    assert manage._load_database_url() == "postgresql://u:p@postgres:5432/perfectday"


def test_database_url_falls_back_to_the_env_file(manage, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / ".env").write_text(
        'OTHER=1\nDATABASE_URL_SYNC="postgresql://u:p@localhost:5432/dev"\n'
    )
    monkeypatch.setattr(manage, "REPO_ROOT", tmp_path)
    assert manage._load_database_url() == "postgresql://u:p@localhost:5432/dev"


# --- users purge-smoke: removes every account the smoke test creates -------------

SMOKE_EMAILS = ["smoke+1794928379@example.com", "smoke2+1794928379@example.com"]
LOOK_ALIKES = [
    "smoke@example.com",
    "smoke+@example.com",
    "smoke+12a@example.com",
    "smoke+1@example.org",
    "xsmoke+1@example.com",
    "smoke+1@example.com.evil.test",
    "smoke3+1@example.com",
]


def _emails(sync_engine) -> set[str]:
    with sync_engine.connect() as conn:
        return set(conn.execute(sa.text("SELECT email FROM users")).scalars())


def _make_smoke_user_with_a_diary(sync_engine, email: str) -> None:
    # Mirrors what scripts/smoke-test.sh leaves behind: a diary (with its scan job)
    # and an entry, so the delete has to cascade through them.
    from datetime import date

    from app.models import Diary, Entry, ScanJob

    with Session(sync_engine) as db:
        user = User(id=uuid.uuid4(), email=email)
        db.add(user)
        db.flush()
        diary = Diary(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Smoke Test Diary",
            slug=f"smoke-{uuid.uuid4().hex[:6]}",
            timezone="UTC",
        )
        db.add(diary)
        db.flush()
        db.add(ScanJob(diary_id=diary.id))
        db.add(
            Entry(
                id=uuid.uuid4(),
                diary_id=diary.id,
                entry_date=date(2026, 11, 17),
                title="Smoke Entry",
                body_markdown="Hello world.",
                status="draft",
                created_by="manual",
                body_source="user",
            )
        )
        db.commit()


def test_purge_smoke_deletes_every_smoke_account_and_its_data(manage, sync_engine) -> None:
    for email in SMOKE_EMAILS:
        _make_smoke_user_with_a_diary(sync_engine, email)
    _make_smoke_user_with_a_diary(sync_engine, "smoke+1700000000@example.com")  # an old, failed run

    result = CliRunner().invoke(manage.cli, ["users", "purge-smoke"])

    assert result.exit_code == 0, result.output
    assert "Deleted 3 smoke-test user(s)" in result.output
    remaining = _emails(sync_engine)
    assert not remaining & {*SMOKE_EMAILS, "smoke+1700000000@example.com"}
    with sync_engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM diaries WHERE name = 'Smoke Test Diary'")
        ).scalar_one() == 0


def test_purge_smoke_keeps_look_alike_accounts(manage, sync_engine) -> None:
    for email in LOOK_ALIKES:
        _make_user(sync_engine, email)
    _make_user(sync_engine, SMOKE_EMAILS[0])

    result = CliRunner().invoke(manage.cli, ["users", "purge-smoke"])

    assert result.exit_code == 0, result.output
    remaining = _emails(sync_engine)
    assert set(LOOK_ALIKES) <= remaining
    assert SMOKE_EMAILS[0] not in remaining


def test_purge_smoke_with_nothing_to_delete_succeeds(manage, sync_engine) -> None:
    result = CliRunner().invoke(manage.cli, ["users", "purge-smoke"])
    assert result.exit_code == 0, result.output
    assert "Deleted 0 smoke-test user(s)" in result.output


def test_purge_smoke_never_asks_for_confirmation(manage, sync_engine, monkeypatch) -> None:
    # It runs unattended at the end of every smoke test, including inside a deploy.
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("purge-smoke prompted"))
    _make_user(sync_engine, SMOKE_EMAILS[1])
    result = CliRunner().invoke(manage.cli, ["users", "purge-smoke"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Row 51: --hard deletes clean storage the way the app does
# ---------------------------------------------------------------------------


@pytest.fixture
def app_storage(db_url, s3_server, s3_endpoint, s3_client, photos_bucket, monkeypatch):
    """Point the app's async engine and storage at the test containers, as app.env does."""
    import app.core.database as db_module
    import app.core.dependencies as deps
    from app.core.config import get_settings

    cfg = s3_server.get_config()
    for name, value in {
        "DATABASE_URL": db_url,
        "S3_ENDPOINT_URL": s3_endpoint,
        "S3_ACCESS_KEY": cfg["access_key"],
        "S3_SECRET_KEY": cfg["secret_key"],
        "S3_BUCKET_PHOTOS": photos_bucket,
        "S3_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None
    deps._s3_client = s3_client
    yield s3_client, photos_bucket
    db_module._engine = None
    db_module._session_factory = None
    deps._s3_client = None
    get_settings.cache_clear()


def _keys(s3, bucket: str, prefix: str) -> list[str]:
    return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])]


def _store_photo(db: Session, s3, bucket: str, user_id: uuid.UUID):
    from datetime import UTC, datetime

    from app.core.photo_crypto import generate_dek, wrap_dek
    from app.models import Photo

    pid = uuid.uuid4()
    photo = Photo(
        id=pid,
        user_id=user_id,
        s3_key=f"{user_id}/{pid}.enc",
        thumbnail_s3_key=f"{user_id}/{pid}_thumb.enc",
        mime_type="image/jpeg",
        bytes=1,
        finalized_at=datetime.now(tz=UTC),
        dek_ciphertext=wrap_dek(generate_dek(), user_id),
        source="upload",
    )
    db.add(photo)
    for key in (photo.s3_key, photo.thumbnail_s3_key):
        s3.put_object(Bucket=bucket, Key=key, Body=b"x")
    return photo


def _diary_with_entry(db: Session, user_id: uuid.UUID):
    from datetime import date

    from app.models import Diary, Entry, ScanJob

    diary = Diary(
        id=uuid.uuid4(),
        owner_user_id=user_id,
        name="D",
        slug=f"d-{uuid.uuid4().hex[:6]}",
        timezone="UTC",
    )
    db.add(diary)
    db.flush()
    db.add(ScanJob(diary_id=diary.id))
    entry = Entry(
        id=uuid.uuid4(),
        diary_id=diary.id,
        entry_date=date(2026, 11, 20),
        status="draft",
        created_by="manual",
    )
    db.add(entry)
    db.flush()
    return diary, entry


def test_cli_hard_delete_active_user_scrubs_storage(manage, sync_engine, app_storage) -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.core.security import encrypt_oauth_token
    from app.models import EntryPhoto, OAuthToken
    from app.services.google_revoke import GOOGLE_REVOKE_URL

    s3, bucket = app_storage
    email = "cli-hard@example.com"
    with Session(sync_engine) as db:
        user = User(id=uuid.uuid4(), email=email)
        db.add(user)
        db.flush()
        _, entry = _diary_with_entry(db, user.id)
        photo = _store_photo(db, s3, bucket, user.id)
        db.flush()
        db.add(EntryPhoto(entry_id=entry.id, photo_id=photo.id))
        _store_photo(db, s3, bucket, user.id)  # in the library, on no entry
        s3.put_object(Bucket=bucket, Key=f"{user.id}/avatar/a.enc", Body=b"x")
        db.add(
            OAuthToken(
                user_id=user.id,
                provider="google",
                access_token_ciphertext=encrypt_oauth_token("access-1"),
                refresh_token_ciphertext=encrypt_oauth_token("refresh-1"),
                scopes_granted=["calendar.readonly"],
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        db.commit()
        user_id = user.id
    assert len(_keys(s3, bucket, f"{user_id}/")) == 5

    post = AsyncMock(return_value=httpx.Response(200, json={}))
    with patch("httpx.AsyncClient.post", post):
        result = CliRunner().invoke(
            manage.cli, ["users", "delete", "--email", email, "--hard"], input="yes\n"
        )

    assert result.exit_code == 0, result.output
    assert email not in _emails(sync_engine)
    assert _keys(s3, bucket, f"{user_id}/") == []
    assert [c.kwargs["data"] for c in post.await_args_list if c.args[0] == GOOGLE_REVOKE_URL] == [
        {"token": "refresh-1"}
    ]


def test_cli_hard_delete_diary_keeps_shared_photo(manage, sync_engine, app_storage) -> None:
    from app.models import EntryPhoto, Photo

    s3, bucket = app_storage
    with Session(sync_engine) as db:
        user = User(id=uuid.uuid4(), email="cli-diary@example.com")
        db.add(user)
        db.flush()
        diary_1, entry_1 = _diary_with_entry(db, user.id)
        _, entry_2 = _diary_with_entry(db, user.id)
        only_here = _store_photo(db, s3, bucket, user.id)
        shared = _store_photo(db, s3, bucket, user.id)
        db.flush()
        db.add_all(
            [
                EntryPhoto(entry_id=entry_1.id, photo_id=only_here.id),
                EntryPhoto(entry_id=entry_1.id, photo_id=shared.id),
                EntryPhoto(entry_id=entry_2.id, photo_id=shared.id),
            ]
        )
        db.commit()
        diary_id, only_id, shared_id = diary_1.id, only_here.id, shared.id
        only_keys = [only_here.s3_key, only_here.thumbnail_s3_key]
        shared_keys = [shared.s3_key, shared.thumbnail_s3_key]

    result = CliRunner().invoke(
        manage.cli, ["diaries", "delete", "--diary-id", str(diary_id), "--hard"], input="yes\n"
    )

    assert result.exit_code == 0, result.output
    with Session(sync_engine) as db:
        assert db.get(Photo, only_id) is None
        assert db.get(Photo, shared_id) is not None
    for key in only_keys:
        assert _keys(s3, bucket, key) == []
    for key in shared_keys:
        assert _keys(s3, bucket, key) == [key]


def test_cli_hard_delete_entry_keeps_library_photo(manage, sync_engine, app_storage) -> None:
    from app.models import EntryPhoto, Photo

    s3, bucket = app_storage
    with Session(sync_engine) as db:
        user = User(id=uuid.uuid4(), email="cli-entry@example.com")
        db.add(user)
        db.flush()
        _, entry = _diary_with_entry(db, user.id)
        photo = _store_photo(db, s3, bucket, user.id)
        db.flush()
        db.add(EntryPhoto(entry_id=entry.id, photo_id=photo.id))
        db.commit()
        entry_id, photo_id, key = entry.id, photo.id, photo.s3_key

    result = CliRunner().invoke(
        manage.cli, ["entries", "delete", "--entry-id", str(entry_id), "--hard"], input="yes\n"
    )

    assert result.exit_code == 0, result.output
    with Session(sync_engine) as db:
        assert db.get(Photo, photo_id) is not None
    assert _keys(s3, bucket, key) == [key]


def test_cli_hard_delete_with_storage_down_leaves_it_due(manage, sync_engine, app_storage) -> None:
    """Storage fails: the command says so, and the account is left for the hourly job."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    from app.core.dependencies import get_s3

    s3, bucket = app_storage
    email = "cli-hard-down@example.com"
    with Session(sync_engine) as db:
        user = User(id=uuid.uuid4(), email=email)
        db.add(user)
        db.commit()
        user_id = user.id
    s3.put_object(Bucket=bucket, Key=f"{user_id}/avatar/a.enc", Body=b"x")

    down = ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "down"}}, "Delete")
    with patch.object(get_s3(), "delete_objects", side_effect=down):
        result = CliRunner().invoke(
            manage.cli, ["users", "delete", "--email", email, "--hard"], input="yes\n"
        )

    assert result.exit_code != 0
    assert "hourly job will finish it" in result.output
    with Session(sync_engine) as db:
        row = db.get(User, user_id)
        assert row is not None and row.deleted_at is not None and row.hard_delete_after is not None
