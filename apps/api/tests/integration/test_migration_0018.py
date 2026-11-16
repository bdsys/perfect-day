"""Migration 0018: reservation keys on generations; sweepable spends."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0018_test"


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


def test_existing_spends_not_sweepable(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0017")
    engine = sa.create_engine(scratch_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, subscription_tier) "
                    "VALUES (gen_random_uuid(), 'a@example.com', 'free')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO ai_draft_spends (id, user_id, month, key) "
                    "SELECT gen_random_uuid(), id, '2026-11', 'old' FROM users"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "0018")
    engine = sa.create_engine(scratch_db_url)
    try:
        with engine.connect() as conn:
            sweepable = conn.execute(sa.text("SELECT sweepable FROM ai_draft_spends")).scalar_one()
            assert sweepable is False
        insp = sa.inspect(engine)
        assert "ix_llm_generations_reservation_key" in {
            i["name"] for i in insp.get_indexes("llm_generations")
        }
    finally:
        engine.dispose()

    command.downgrade(cfg, "0017")
    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        assert "sweepable" not in {c["name"] for c in insp.get_columns("ai_draft_spends")}
        assert "reservation_key" not in {c["name"] for c in insp.get_columns("llm_generations")}
    finally:
        engine.dispose()
