"""Migration 0016: photos.display_attempted_at and ix_entry_photos_photo_id, up and down."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0016_test"


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


def _state(url: str) -> tuple[bool, bool]:
    engine = sa.create_engine(url)
    try:
        insp = sa.inspect(engine)
        cols = {c["name"] for c in insp.get_columns("photos")}
        idx = {i["name"] for i in insp.get_indexes("entry_photos")}
        return "display_attempted_at" in cols, "ix_entry_photos_photo_id" in idx
    finally:
        engine.dispose()


def test_migration_0016_up_down(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0015")
    assert _state(scratch_db_url) == (False, False)

    command.upgrade(cfg, "0016")
    assert _state(scratch_db_url) == (True, True)

    command.downgrade(cfg, "0015")
    assert _state(scratch_db_url) == (False, False)
