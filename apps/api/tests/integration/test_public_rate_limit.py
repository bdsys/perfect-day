"""Per-IP rate limits on the public API (the normal client fixture disables them)."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.middleware.rate_limit import PUBLIC_READ_LIMIT, public_limiter


@pytest_asyncio.fixture
async def limited_app(db_url, redis_container, s3_endpoint, s3_server, photos_bucket):
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}"
        f":{redis_container.get_exposed_port(6379)}/0"
    )
    cfg = s3_server.get_config()
    env = {
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
    with patch.dict(os.environ, env):
        import app.core.database as db_module
        from app.core.config import get_settings

        get_settings.cache_clear()
        engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
        db_module._engine = engine
        db_module._session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        public_limiter.reset()
        from app.main import create_app

        try:
            yield create_app()
        finally:
            public_limiter.reset()
            await engine.dispose()
            db_module._engine = None
            db_module._session_factory = None
            from app.core.dependencies import close_redis_for_current_loop

            await close_redis_for_current_loop()


def _client(app, ip: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 1234)), base_url="http://test")


async def test_public_reads_limited_per_ip(limited_app):
    assert PUBLIC_READ_LIMIT == "60/minute"
    async with _client(limited_app, "198.51.100.1") as a, _client(limited_app, "198.51.100.2") as b:
        for _ in range(60):
            assert (await a.get("/v1/public/nope")).status_code == 404
        r = await a.get("/v1/public/nope")
        assert r.status_code == 429
        assert r.headers["x-robots-tag"] == "noindex, nofollow"
        assert (await b.get("/v1/public/nope")).status_code == 404
    assert httpx  # transport module used above
