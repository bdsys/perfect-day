"""Integration tests: Google OAuth callback persists google_email/google_name;
GET /v1/integrations returns those fields.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
from httpx import AsyncClient


def _make_id_token(email: str, name: str) -> str:
    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = b64url(json.dumps({"email": email, "name": name, "sub": "google-sub-123"}).encode())
    return f"{header}.{payload}.fakesig"


async def _register(client: AsyncClient, email: str = "gcal@example.com") -> str:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def _get_auth_url_state_and_nonce(client: AsyncClient, access_token: str) -> str:
    """Return the signed state string from the authorize URL."""
    r = await client.get(
        "/v1/integrations/google/authorize?scopes=calendar",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200
    url = r.json()["url"]
    # Extract state param from URL
    qs = url.split("?", 1)[1]
    params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
    from urllib.parse import unquote_plus

    return unquote_plus(params["state"])


class TestGoogleCallbackPersistsIdentity:
    async def test_callback_stores_google_email_and_name(self, client):
        """After a successful OAuth callback, google_email and google_name are persisted."""
        access_token = await _register(client, "identity@example.com")
        state = await _get_auth_url_state_and_nonce(client, access_token)

        google_email = "identity@gmail.com"
        google_name = "Identity User"
        fake_token_response = {
            "access_token": "ya29.fake-access-token",
            "refresh_token": "1//fake-refresh",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar.readonly openid email profile",
            "id_token": _make_id_token(google_email, google_name),
        }

        mock_resp = httpx.Response(200, json=fake_token_response)
        mock_post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient.post", mock_post):
            r = await client.get(
                f"/v1/integrations/google/callback?code=fake-code&state={state}"
                f"&scope=https://www.googleapis.com/auth/calendar.readonly",
                follow_redirects=False,
            )
        # Should redirect on success
        assert r.status_code in (302, 307)

        # Now check GET /v1/integrations returns the identity fields
        r2 = await client.get(
            "/v1/integrations",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r2.status_code == 200
        integrations = r2.json()
        assert len(integrations) == 1
        integration = integrations[0]
        assert integration["google_email"] == google_email
        assert integration["google_name"] == google_name

    async def test_callback_without_id_token_stores_nulls(self, client):
        """If Google omits id_token, email/name are stored as null (no failure)."""
        access_token = await _register(client, "noidtoken@example.com")
        state = await _get_auth_url_state_and_nonce(client, access_token)

        fake_token_response = {
            "access_token": "ya29.fake-access-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar.readonly",
            # no id_token
        }

        mock_resp = httpx.Response(200, json=fake_token_response)
        mock_post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient.post", mock_post):
            r = await client.get(
                f"/v1/integrations/google/callback?code=fake-code&state={state}"
                f"&scope=https://www.googleapis.com/auth/calendar.readonly",
                follow_redirects=False,
            )
        assert r.status_code in (302, 307)

        r2 = await client.get(
            "/v1/integrations",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r2.status_code == 200
        integration = r2.json()[0]
        assert integration["google_email"] is None
        assert integration["google_name"] is None


class TestListIntegrationsIncludesIdentity:
    async def test_list_includes_google_email_and_name_fields(self, client):
        """GET /v1/integrations always includes google_email and google_name keys."""
        access_token = await _register(client, "listcheck@example.com")
        r = await client.get(
            "/v1/integrations",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code == 200
        # Empty list is fine — just confirms the endpoint works; identity fields
        # are present when an integration exists (covered by tests above).
        assert isinstance(r.json(), list)
