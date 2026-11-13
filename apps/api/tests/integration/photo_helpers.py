"""Shared helpers for photo integration tests."""
from __future__ import annotations

import pathlib

PASSWORD = "Password1!"  # noqa: S105


def read_fixture(name: str) -> bytes:
    return (pathlib.Path(__file__).parent.parent / "fixtures" / "photos" / name).read_bytes()


async def register_and_login(client, email: str) -> dict[str, str]:
    """Register a user, log in, and return the Authorization header."""
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload_photo(client, headers, body: bytes, filename: str = "photo.jpg") -> str:
    """Upload through POST /v1/photos; returns the photo id."""
    r = await client.post(
        "/v1/photos", files={"file": (filename, body, "application/octet-stream")}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]
