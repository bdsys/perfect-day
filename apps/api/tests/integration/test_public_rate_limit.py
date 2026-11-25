"""Per-IP rate limits on the public API (the normal client fixture disables them)."""

from __future__ import annotations

import logging
import os
import uuid
from unittest.mock import patch

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.middleware.rate_limit import PUBLIC_READ_LIMIT, PUBLIC_WRITE_LIMIT, public_limiter
from tests.integration.wish_list_helpers import _add_item, _new_list, _owner, _token_of


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


async def test_refused_request_logs_no_token(limited_app, caplog, monkeypatch):
    """slowapi warns on every refused request; the link token must not be in it."""
    # The migrations fixture runs alembic's fileConfig, which disables loggers that
    # already exist (slowapi's among them). uvicorn's own config leaves them on.
    monkeypatch.setattr(logging.getLogger("slowapi"), "disabled", False)
    token = "SECRETTOKENxyz"  # noqa: S105
    async with _client(limited_app, "198.51.100.9") as a:
        with caplog.at_level(logging.DEBUG, logger="slowapi"):
            for _ in range(61):
                r = await a.get(f"/v1/public/{token}")
            assert r.status_code == 429
            r = await a.post(f"/v1/public/{token}/items/{uuid.uuid4()}/claims", json={"name": "x"})
    assert any(rec.name == "slowapi" for rec in caplog.records)
    assert token not in caplog.text
    for rec in caplog.records:
        assert token not in rec.getMessage()


async def _live_list_with_items(app, n: int) -> tuple[str, list[str]]:
    """A published list with `n` items, set up from an address no limit test uses."""
    async with _client(app, "203.0.113.250") as seed:
        owner, did = await _owner(seed, "rl-o@example.com")
        wl = await _new_list(seed, owner, did)
        items = [(await _add_item(seed, owner, wl["id"], name=f"Item {i}"))["id"] for i in range(n)]
        r = await seed.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
        assert r.status_code == 200, r.text
        return await _token_of(seed, owner, wl["id"]), items


async def test_public_writes_limited_per_ip_across_items_and_claims(limited_app):
    """20 claims an hour per address in all, not per item; undos have their own 20."""
    assert PUBLIC_WRITE_LIMIT == "20/hour"
    token, items = await _live_list_with_items(limited_app, 22)
    async with _client(limited_app, "198.51.100.3") as a, _client(limited_app, "198.51.100.4") as b:
        claims = []
        for item in items[:20]:
            r = await a.post(f"/v1/public/{token}/items/{item}/claims", json={"name": "Sam"})
            assert r.status_code == 201, r.text
            claims.append(r.json())
        r = await a.post(f"/v1/public/{token}/items/{items[20]}/claims", json={"name": "Sam"})
        assert r.status_code == 429
        r = await b.post(f"/v1/public/{token}/items/{items[20]}/claims", json={"name": "Jo"})
        assert r.status_code == 201, r.text
        b_claim = r.json()

        # Undo is counted apart from claims, and across every claim's URL.
        for c in claims:
            r = await a.delete(
                f"/v1/public/{token}/claims/{c['claim_id']}",
                headers={"X-Claim-Secret": c["secret"]},
            )
            assert r.status_code == 204, r.text
        r = await a.delete(
            f"/v1/public/{token}/claims/{b_claim['claim_id']}",
            headers={"X-Claim-Secret": b_claim["secret"]},
        )
        assert r.status_code == 429
        r = await b.delete(
            f"/v1/public/{token}/claims/{b_claim['claim_id']}",
            headers={"X-Claim-Secret": b_claim["secret"]},
        )
        assert r.status_code == 204, r.text
