from __future__ import annotations

import asyncio

import boto3
import redis.asyncio as aioredis

from app.core.config import get_settings

# Keyed by event loop id so each event loop (e.g. each pytest-asyncio test) gets its own
# client. In production a single loop lives for the process lifetime, so this is a singleton.
_redis_clients: dict[int, aioredis.Redis] = {}
_s3_client = None


def get_redis() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    client = _redis_clients.get(id(loop))
    if client is None:
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _redis_clients[id(loop)] = client
    return client


async def close_redis_for_current_loop() -> None:
    loop = asyncio.get_running_loop()
    client = _redis_clients.pop(id(loop), None)
    if client is not None:
        await client.aclose()


def get_s3():
    global _s3_client
    if _s3_client is None:
        settings = get_settings()
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
    return _s3_client
