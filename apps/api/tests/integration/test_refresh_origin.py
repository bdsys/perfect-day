"""The refresh endpoint accepts the production hostnames and rejects others (P-9).

The Origin check runs before the cookie check, so with no cookie an allowed
origin gets 401 (no token) and a refused origin gets 403 (forbidden_origin).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.parametrize(
    "origin",
    [
        "https://perfectday.andrewlass.com",
        "https://perfectday-api.andrewlass.com",
        "http://localhost:3000",
    ],
)
async def test_refresh_allows_the_app_origins(client: AsyncClient, origin: str):
    r = await client.post("/v1/auth/refresh", headers={"Origin": origin})
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(
    "origin",
    [
        # The old second-level names: Cloudflare's free certificate never covered them.
        "https://diary.perfectday.andrewlass.com",
        "https://api.diary.perfectday.andrewlass.com",
        "https://evil.example.com",
    ],
)
async def test_refresh_refuses_other_origins(client: AsyncClient, origin: str):
    r = await client.post("/v1/auth/refresh", headers={"Origin": origin})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["message"] == "forbidden_origin"
