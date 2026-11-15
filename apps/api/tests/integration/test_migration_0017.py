"""Migration 0017: tiers renamed to plus/family; ai_draft_spends created."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from alembic import command

SCRATCH_DB = "migration_0017_test"


@pytest.fixture
def scratch_db_url(db_url_sync):
    admin = sa.create_engine(db_url_sync, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        conn.execute(sa.text(f"CREATE DATABASE {SCRATCH_DB}"))
    yield db_url_sync.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
    admin.dispose()


def _alembic(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tiers(url: str) -> dict[str, str]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT email, subscription_tier FROM users"))
            return {r[0]: r[1] for r in rows}
    finally:
        engine.dispose()


def _insert_user(url: str, email: str, tier: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, subscription_tier) "
                    "VALUES (gen_random_uuid(), :e, :t)"
                ),
                {"e": email, "t": tier},
            )
    finally:
        engine.dispose()


def test_migration_0017_up_down(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0016")
    _insert_user(scratch_db_url, "a@example.com", "free")
    _insert_user(scratch_db_url, "b@example.com", "tier1")
    _insert_user(scratch_db_url, "c@example.com", "tier2")

    command.upgrade(cfg, "0017")
    assert _tiers(scratch_db_url) == {
        "a@example.com": "free",
        "b@example.com": "plus",
        "c@example.com": "family",
    }
    with pytest.raises(IntegrityError, match="ck_users_subscription_tier"):
        _insert_user(scratch_db_url, "d@example.com", "tier1")

    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        assert "ai_draft_spends" in insp.get_table_names()
        uniques = [u["column_names"] for u in insp.get_unique_constraints("ai_draft_spends")]
        unique_idx = [
            i["column_names"] for i in insp.get_indexes("ai_draft_spends") if i["unique"]
        ]
        assert ["key"] in uniques + unique_idx
        assert "ix_ai_draft_spends_user_month" in {
            i["name"] for i in insp.get_indexes("ai_draft_spends")
        }
    finally:
        engine.dispose()

    command.downgrade(cfg, "0016")
    assert _tiers(scratch_db_url) == {
        "a@example.com": "free",
        "b@example.com": "tier1",
        "c@example.com": "tier2",
    }
    engine = sa.create_engine(scratch_db_url)
    try:
        assert "ai_draft_spends" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
