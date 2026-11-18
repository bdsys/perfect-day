"""Shared fixtures for integration tests: testcontainers, async client, DB session."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

# ---------------------------------------------------------------------------
# Session-scoped containers (start once, reused across all tests)
# ---------------------------------------------------------------------------

# Photo storage is Garage, as in every other environment (tracker row 41). The image
# tag is read from docker-compose.yml, the one place it is pinned, and the server
# uses the same committed config file as dev and the NUC.
REPO_ROOT = Path(__file__).resolve().parents[4]
GARAGE_TOML = REPO_ROOT / "deploy" / "garage" / "garage.toml"
S3_TEST_ACCESS_KEY = "perfectday-test"
S3_TEST_SECRET_KEY = "perfectday-test-secret-01"  # noqa: S105  # throwaway container


def garage_image() -> str:
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    return compose["services"]["garage"]["image"]


class _S3Server:
    """The running Garage container, with the connection details tests need."""

    def __init__(self, container: DockerContainer):
        self.container = container

    def get_config(self) -> dict[str, str]:
        host = self.container.get_container_host_ip()
        port = self.container.get_exposed_port(3900)
        return {
            "endpoint": f"{host}:{port}",
            "access_key": S3_TEST_ACCESS_KEY,
            "secret_key": S3_TEST_SECRET_KEY,
        }


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session", autouse=True)
def isolate_redis(redis_container):
    """Point every integration test at the test Redis, never at localhost:6379.

    A developer's `make up` stack publishes Redis on 127.0.0.1:6379. Worker code
    called without the `client` fixture would otherwise reach it: redraft keys and
    real Celery tasks leaked into the dev queue, and a test's result depended on
    whether that stack happened to be running.
    """
    from app.core.config import get_settings
    from app.workers.celery_app import celery_app

    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}"
        f":{redis_container.get_exposed_port(6379)}/0"
    )
    overrides = {
        "REDIS_URL": redis_url,
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
    }
    saved = (celery_app.conf.broker_url, celery_app.conf.result_backend)
    with patch.dict(os.environ, overrides):
        get_settings.cache_clear()
        celery_app.conf.broker_url = redis_url
        celery_app.conf.result_backend = redis_url
        try:
            yield redis_url
        finally:
            celery_app.conf.broker_url, celery_app.conf.result_backend = saved
            get_settings.cache_clear()


@pytest.fixture(scope="session")
def s3_server():
    container = (
        DockerContainer(garage_image())
        .with_exposed_ports(3900)
        .with_volume_mapping(str(GARAGE_TOML), "/etc/garage.toml", "ro")
        .with_env("GARAGE_DEFAULT_ACCESS_KEY", S3_TEST_ACCESS_KEY)
        .with_env("GARAGE_DEFAULT_SECRET_KEY", S3_TEST_SECRET_KEY)
        .with_env("GARAGE_RPC_SECRET", "0" * 64)
        .with_command("/garage server --single-node --default-access-key")
        .waiting_for(LogMessageWaitStrategy("S3 API server listening").with_startup_timeout(30))
    )
    with container:
        yield _S3Server(container)


@pytest.fixture(scope="session")
def s3_endpoint(s3_server):
    cfg = s3_server.get_config()
    return f"http://{cfg['endpoint']}"


@pytest.fixture(scope="session")
def s3_client(s3_server, s3_endpoint):
    import boto3

    cfg = s3_server.get_config()
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
                "photos, diary_photos, entry_photos, enrichments, magic_link_tokens, "
                "social_identities "
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
    db_url, redis_container, s3_server, s3_endpoint, photos_bucket
) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the FastAPI app with testcontainer DB + Redis + Garage."""
    redis_url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
    cfg = s3_server.get_config()

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


# ---------------------------------------------------------------------------
# Common model factories (as pytest fixtures)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def make_user(db_session):
    async def _mk(
        email: str | None = None,
        password: str = "Password1!",  # noqa: S107
        display_name: str | None = None,
        subscription_tier: str = "free",
    ):
        import uuid as _uuid

        from app.core.security import hash_password
        from app.models import User

        u = User(
            email=email or f"test-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password(password),
            display_name=display_name,
            subscription_tier=subscription_tier,
        )
        db_session.add(u)
        await db_session.commit()
        await db_session.refresh(u)
        return u

    return _mk


@pytest_asyncio.fixture
async def make_diary_for_user(db_session):
    async def _mk(user, lat=None, lon=None, timezone="America/Chicago"):
        import uuid as _uuid

        from app.models import Diary, ScanJob

        slug = f"test-diary-{_uuid.uuid4().hex[:6]}"
        d = Diary(
            owner_user_id=user.id,
            name="test-diary",
            slug=slug,
            timezone=timezone,
            tone_hint="warm, narrative",
            subject_relation="self",
            lat=lat,
            lon=lon,
        )
        db_session.add(d)
        scan_job = ScanJob(diary=d)
        db_session.add(scan_job)
        await db_session.commit()
        await db_session.refresh(d)
        return d

    return _mk
