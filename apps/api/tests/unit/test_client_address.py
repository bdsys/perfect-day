"""The API's rate limits key on the visitor, not on the edge proxy (tracker row 71).

In production every request reaches uvicorn from the Caddy edge container. Caddy puts
exactly one address, the visitor's, in X-Forwarded-For (deploy/caddy/Caddyfile.tmpl).
uvicorn only believes that header from the addresses in FORWARDED_ALLOW_IPS, set on
the api service in docker-compose.yml. These tests run uvicorn's own proxy-headers
middleware with that compose value in front of the real limiter key function.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.middleware.rate_limit import _get_user_id_or_ip

REPO = Path(__file__).resolve().parents[4]


def _api_env() -> dict:
    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    return services["api"]["environment"]


def _trusted() -> str:
    return _api_env()["FORWARDED_ALLOW_IPS"]


async def _key(request: Request) -> PlainTextResponse:
    return PlainTextResponse(_get_user_id_or_ip(request))


def _app() -> ProxyHeadersMiddleware:
    inner = Starlette(routes=[Route("/", _key)])
    return ProxyHeadersMiddleware(inner, trusted_hosts=_trusted())


async def _rate_key(client_ip: str, headers: dict[str, str]) -> str:
    transport = httpx.ASGITransport(app=_app(), client=(client_ip, 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return (await client.get("/", headers=headers)).text


def test_compose_trusts_only_docker_networks():
    # The NUC's app network is 172.18.0.0/16 (2026-11-23); Docker hands out 172.16.0.0/12.
    # Never the home LAN (192.168.0.0/16): the FortiGate is there.
    assert _trusted() == "172.16.0.0/12"


async def test_visitor_address_from_the_edge_is_the_rate_limit_key():
    key = await _rate_key("172.18.0.9", {"X-Forwarded-For": "203.0.113.7"})
    assert key == "203.0.113.7"


async def test_two_visitors_through_the_edge_get_separate_keys():
    first = await _rate_key("172.18.0.9", {"X-Forwarded-For": "203.0.113.7"})
    second = await _rate_key("172.18.0.9", {"X-Forwarded-For": "198.51.100.20"})
    assert first != second


async def test_forwarded_header_from_outside_docker_is_ignored():
    key = await _rate_key("192.168.1.50", {"X-Forwarded-For": "203.0.113.7"})
    assert key == "192.168.1.50"
