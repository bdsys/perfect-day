"""Shared fixtures for integration tests: testcontainers, async client, DB session."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

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


@pytest.fixture(scope="session")
def minio_container():
    with MinioContainer(image="minio/minio:latest") as m:
        yield m


@pytest.fixture(scope="session")
def s3_endpoint(minio_container):
    cfg = minio_container.get_config()
    return f"http://{cfg['endpoint']}"


@pytest.fixture(scope="session")
def s3_client(minio_container, s3_endpoint):
    import boto3

    cfg = minio_container.get_config()
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="us-east-1",
    )


@pytest.fixture(scope="session")
def photos_bucket(s3_client):
    name = "photos-test"
    s3_client.create_bucket(Bucket=name)
    return name


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


@pytest.fixture(scope="module")
def sync_engine(db_url_sync):
    engine = sa.create_engine(db_url_sync)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Per-test DB cleanup (synchronous — avoids event-loop scope issues)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def truncate_tables(sync_engine, run_migrations):
    yield
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE users, diaries, entries, events, scan_jobs, "
                "oauth_tokens, refresh_tokens, audit_log, llm_generations, "
                "entry_edit_diffs, diary_permissions, invitations, scan_runs, "
                "backfill_runs, diary_calendar_filters, notification_preferences, "
                "notifications, auto_creation_rules, entry_rule_matches, rule_series_claims, "
                "photos, diary_photos, entry_photos "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Async engine + session (function-scoped — lives in the per-test event loop)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_engine(db_url):
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db_url, redis_container, minio_container, s3_endpoint, photos_bucket
) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the FastAPI app with testcontainer DB + Redis + MinIO."""
    redis_url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
    cfg = minio_container.get_config()

    env_overrides = {
        "DATABASE_URL": db_url,
        "DATABASE_URL_SYNC": db_url.replace("+asyncpg", ""),
        "REDIS_URL": redis_url,
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
        "ENV": "test",
        "S3_ENDPOINT_URL": s3_endpoint,
        "S3_ACCESS_KEY": cfg["access_key"],
        "S3_SECRET_KEY": cfg["secret_key"],
        "S3_BUCKET_PHOTOS": "photos-test",
        "S3_REGION": "us-east-1",
        "MASTER_SECRET": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
    }

    with patch.dict(os.environ, env_overrides):
        import app.core.database as db_module

        engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
        db_module._engine = engine
        db_module._session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

        from app.core.config import get_settings

        get_settings.cache_clear()

        import app.core.dependencies as deps_module

        deps_module._s3_client = None

        import app.middleware.rate_limit as _rl

        def _noop_check(request, *args, **kwargs):
            request.state.view_rate_limit = None

        with (
            patch.object(_rl.auth_limiter, "_check_request_limit", new=_noop_check),
            patch.object(_rl.limiter, "_check_request_limit", new=_noop_check),
        ):
            from app.main import create_app

            app_instance = create_app()

            async with AsyncClient(
                transport=ASGITransport(app=app_instance),
                base_url="http://test",
            ) as ac:
                yield ac

        await engine.dispose()
        db_module._engine = None
        db_module._session_factory = None
        from app.core.dependencies import close_redis_for_current_loop

        await close_redis_for_current_loop()
