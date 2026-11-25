"""Migration 0022: public_links table."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0022_test"


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


def test_upgrade_creates_public_links_and_downgrade_drops(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0022")
    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        cols = {c["name"] for c in insp.get_columns("public_links")}
        assert {
            "id",
            "diary_id",
            "kind",
            "target_id",
            "token_hash",
            "token_encrypted",
            "revoked_at",
            "created_at",
            "updated_at",
        } <= cols
        assert "ix_public_links_kind_target_id" in {
            i["name"] for i in insp.get_indexes("public_links")
        }
    finally:
        engine.dispose()

    command.downgrade(cfg, "0021")
    engine = sa.create_engine(scratch_db_url)
    try:
        assert "public_links" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
