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
