"""Migration 0023: wish list tables."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0023_test"


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


def test_upgrade_creates_wish_list_tables_and_downgrade_drops(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "0023")
    engine = sa.create_engine(scratch_db_url)
    try:
        insp = sa.inspect(engine)
        assert {c["name"] for c in insp.get_columns("wish_lists")} >= {
            "id",
            "diary_id",
            "title",
            "list_date",
            "pasted_list_url",
            "status",
            "published_at",
            "created_by_user_id",
            "created_at",
            "updated_at",
        }
        assert {c["name"] for c in insp.get_columns("wish_list_items")} >= {
            "id",
            "wish_list_id",
            "position",
            "name",
            "url",
            "note",
            "many",
        }
        assert {c["name"] for c in insp.get_columns("wish_list_claims")} >= {
            "id",
            "item_id",
            "user_id",
            "name",
            "secret_hash",
            "created_at",
        }
        assert "uq_wish_list_claims_item_user" in {
            i["name"] for i in insp.get_indexes("wish_list_claims")
        }
    finally:
        engine.dispose()

    command.downgrade(cfg, "0022")
    engine = sa.create_engine(scratch_db_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not tables & {"wish_lists", "wish_list_items", "wish_list_claims"}
    finally:
        engine.dispose()
