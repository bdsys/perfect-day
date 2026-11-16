"""Integration tests never reach a developer's own Redis on localhost:6379.

A dev stack (`make up`) publishes Redis on 127.0.0.1:6379. Worker code called
without the `client` fixture used to fall through to it, leaking redraft keys
and real Celery tasks into the dev queue.
"""

from __future__ import annotations


def _test_redis_url(redis_container) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


async def test_get_redis_uses_the_test_container_without_the_client_fixture(redis_container):
    from app.core.dependencies import get_redis

    kwargs = get_redis().connection_pool.connection_kwargs
    expected = _test_redis_url(redis_container)
    assert f"redis://{kwargs['host']}:{kwargs['port']}/0" == expected


def test_celery_publishes_to_the_test_container(redis_container):
    from app.workers.celery_app import celery_app

    assert celery_app.conf.broker_url == _test_redis_url(redis_container)
