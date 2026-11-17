"""Migration 0019: ai_draft_jobs table."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0019_test"


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


def test_upgrade_creates_and_downgrade_drops_ai_draft_jobs(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0019")
    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        assert "ai_draft_jobs" in insp.get_table_names()
        assert "ix_ai_draft_jobs_entry_id" in {i["name"] for i in insp.get_indexes("ai_draft_jobs")}
    finally:
        engine.dispose()

    command.downgrade(cfg, "0018")
    engine = sa.create_engine(scratch_db_url)
    try:
        assert "ai_draft_jobs" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
