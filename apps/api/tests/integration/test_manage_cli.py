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
