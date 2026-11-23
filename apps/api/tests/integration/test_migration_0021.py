"""Migration 0021: diary sharing columns and invitations index."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0021_test"


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


def test_upgrade_backfills_owned_since_and_downgrade_drops(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0020")
    engine = sa.create_engine(scratch_db_url)
    try:
        with engine.begin() as conn:
            user_id = conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, display_name) "
                    "VALUES (gen_random_uuid(), 'o@example.com', 'O') RETURNING id"
                )
            ).scalar_one()
            diary_id = conn.execute(
                sa.text(
                    "INSERT INTO diaries (id, owner_user_id, name, slug, timezone, created_at) "
                    "VALUES (gen_random_uuid(), :u, 'D', 'd', 'UTC', '2025-01-02T00:00:00Z') "
                    "RETURNING id"
                ),
                {"u": user_id},
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO invitations "
                    "(id, diary_id, invited_email, role, token, expires_at) "
                    "VALUES (gen_random_uuid(), :d, 'x@example.com', 'viewer', 'rawtoken', now())"
                ),
                {"d": diary_id},
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "0021")
    engine = sa.create_engine(scratch_db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT owned_since, created_at, handover_to_user_id FROM diaries")
            ).one()
            assert row.owned_since == row.created_at
            assert row.handover_to_user_id is None
            assert conn.execute(sa.text("SELECT count(*) FROM invitations")).scalar_one() == 0
        insp = sa.inspect(engine)
        assert "ix_invitations_diary_id" in {i["name"] for i in insp.get_indexes("invitations")}
    finally:
        engine.dispose()

    command.downgrade(cfg, "0020")
    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        cols = {c["name"] for c in insp.get_columns("diaries")}
        assert "owned_since" not in cols
        assert "handover_to_user_id" not in cols
        assert "ix_invitations_diary_id" not in {i["name"] for i in insp.get_indexes("invitations")}
    finally:
        engine.dispose()
