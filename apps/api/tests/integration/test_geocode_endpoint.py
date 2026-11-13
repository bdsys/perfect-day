"""GET /v1/geocode: the city search behind the place boxes (item 24).

`search_places` is patched: these tests never call Open-Meteo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.services.geocoding import GeocodingUnavailable, Place


def _place(name: str) -> Place:
    return Place(
        name=name, admin1="Washington", country="United States", country_code="US",
        lat=47.9, lon=-122.3, timezone="America/Los_Angeles", feature_code="PPL",
    )  # fmt: skip


async def _auth(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestGeocodeEndpoint:
    async def test_needs_sign_in(self, client: AsyncClient):
        r = await client.get("/v1/geocode", params={"q": "Mukilteo"})
        assert r.status_code == 401

    async def test_returns_up_to_five_places(self, client: AsyncClient):
        auth = await _auth(client, "geo-list@example.com")
        search = AsyncMock(return_value=[_place(f"Town {i}") for i in range(5)])

        with patch("app.routers.v1.geocode.search_places", search):
            r = await client.get("/v1/geocode", params={"q": "Muk"}, headers=auth)

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 5
        assert body[0] == {
            "name": "Town 0", "admin1": "Washington", "country": "United States",
            "lat": 47.9, "lon": -122.3, "timezone": "America/Los_Angeles",
            "label": "Town 0, Washington, United States",
        }  # fmt: skip
        assert search.await_args.args == ("Muk",)
        assert search.await_args.kwargs.get("count") == 5

    async def test_short_query_is_422(self, client: AsyncClient):
        auth = await _auth(client, "geo-short@example.com")
        search = AsyncMock()
        with patch("app.routers.v1.geocode.search_places", search):
            r = await client.get("/v1/geocode", params={"q": "M"}, headers=auth)
        assert r.status_code == 422
        search.assert_not_awaited()

    async def test_geocoder_down_is_503(self, client: AsyncClient):
        auth = await _auth(client, "geo-down@example.com")
        down = AsyncMock(side_effect=GeocodingUnavailable("timeout"))

        with patch("app.routers.v1.geocode.search_places", down):
            r = await client.get("/v1/geocode", params={"q": "Mukilteo"}, headers=auth)

        assert r.status_code == 503
        assert r.json()["error"]["message"] == "geocoding_unavailable"
