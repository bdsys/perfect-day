"""Shared fixtures for integration tests: testcontainers, async client, DB session."""

from __future__ import annotations

import itertools
import os
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

_ip_counter = itertools.count(1)

# ---------------------------------------------------------------------------
# Session-scoped containers (start once, reused across all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


# ---------------------------------------------------------------------------
# Database engine + migrations (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db_url(postgres_container):
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    pw = postgres_container.password
    db = postgres_container.dbname
    return f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}"


@pytest.fixture(scope="session")
def db_url_sync(postgres_container):
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    pw = postgres_container.password
    db = postgres_container.dbname
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


@pytest.fixture(scope="session", autouse=True)
def run_migrations(db_url_sync):
    """Run alembic migrations once per test session."""
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url_sync)
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# Per-test DB cleanup (TRUNCATE all tables)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def truncate_tables(db_engine):
    yield
    async with db_engine.connect() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE users, diaries, entries, events, scan_jobs, "
                "oauth_tokens, refresh_tokens, audit_log, llm_generations, "
                "entry_edit_diffs, diary_permissions, invitations, scan_runs, "
                "backfill_runs, diary_calendar_filters, notification_preferences, "
                "notifications "
                "RESTART IDENTITY CASCADE"
            )
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Engine + session factory (function-scoped to pick up patched env)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine(db_url):
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_engine, redis_container) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the FastAPI app with testcontainer DB + Redis."""
    redis_url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
    db_url_str = str(db_engine.url)

    env_overrides = {
        "DATABASE_URL": db_url_str,
        "DATABASE_URL_SYNC": db_url_str.replace("+asyncpg", ""),
        "REDIS_URL": redis_url,
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
        "ENV": "test",
    }

    with patch.dict(os.environ, env_overrides):
        # Patch the database module to use the test engine
        import app.core.database as db_module

        db_module._engine = db_engine
        db_module._session_factory = async_sessionmaker(
            db_engine, expire_on_commit=False, class_=AsyncSession
        )

        from app.core.config import get_settings

        get_settings.cache_clear()

        from app.main import create_app

        app_instance = create_app()

        n = next(_ip_counter)
        test_ip = f"10.{n // 65536 % 256}.{n // 256 % 256}.{n % 256}"

        import app.middleware.rate_limit as _rl

        with patch.object(_rl.auth_limiter, "_key_func", return_value=test_ip):
            async with AsyncClient(
                transport=ASGITransport(app=app_instance),
                base_url="http://test",
            ) as ac:
                yield ac

        # Reset db module state
        db_module._engine = None
        db_module._session_factory = None
