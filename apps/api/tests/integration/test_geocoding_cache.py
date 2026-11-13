"""Calendar text to a place: the state check, the town check and the cache.

`search_places` is patched: these tests never call Open-Meteo.
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.core.dependencies import get_redis
from app.services.geocoding import GeocodingUnavailable, Place, place_from_event_text


def _place(name="Mukilteo", admin1="Washington", feature_code="PPL") -> Place:
    return Place(
        name=name,
        admin1=admin1,
        country="United States",
        country_code="US",
        lat=47.94454,
        lon=-122.30458,
        timezone="America/Los_Angeles",
        feature_code=feature_code,
    )


def _unique(text: str) -> str:
    """The cache is shared across tests; give each test its own text."""
    return f"Venue {uuid.uuid4().hex[:8]}, {text}"


class TestPlaceFromEventText:
    async def test_a_zip_in_another_state_is_rejected(self, client: AsyncClient):
        search = AsyncMock(return_value=[_place(name="Fredonia", admin1="New York")])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(_unique("Woodinville, WA 98072")) is None

    async def test_a_city_result_that_is_not_a_town_is_rejected(self, client: AsyncClient):
        search = AsyncMock(return_value=[_place(name="Disneyland", feature_code="AMUS")])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(_unique("Anaheim, CA")) is None

    async def test_a_matching_town_is_returned(self, client: AsyncClient):
        search = AsyncMock(return_value=[_place()])
        with patch("app.services.geocoding.search_places", search):
            place = await place_from_event_text(_unique("Mukilteo, WA"))
        assert place is not None
        assert place.label == "Mukilteo, Washington"
        search.assert_awaited_once()
        assert search.await_args.kwargs.get("country_code") == "US"

    async def test_the_second_lookup_comes_from_the_cache(self, client: AsyncClient):
        text = _unique("Mukilteo, WA")
        search = AsyncMock(return_value=[_place()])
        with patch("app.services.geocoding.search_places", search):
            first = await place_from_event_text(text)
            second = await place_from_event_text(text)
        assert first == second
        assert search.await_count == 1

    async def test_a_definite_no_match_is_cached_too(self, client: AsyncClient):
        text = _unique("Nowhereville, WA")
        search = AsyncMock(return_value=[])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(text) is None
            assert await place_from_event_text(text) is None
        assert search.await_count == 1

    async def test_geocoder_down_gives_no_place_and_caches_nothing(self, client: AsyncClient):
        text = _unique("Mukilteo, WA")
        down = AsyncMock(side_effect=GeocodingUnavailable("timeout"))
        with patch("app.services.geocoding.search_places", down):
            assert await place_from_event_text(text) is None
        key = "geocode:" + hashlib.sha256(text.encode()).hexdigest()
        assert await get_redis().get(key) is None

    async def test_the_cache_key_is_a_hash_and_the_text_is_not_stored(self, client: AsyncClient):
        text = _unique("Mukilteo Library, 4675 Harbour Pointe Blvd, Mukilteo, WA")
        with patch("app.services.geocoding.search_places", AsyncMock(return_value=[_place()])):
            await place_from_event_text(text)
        redis = get_redis()
        key = "geocode:" + hashlib.sha256(text.encode()).hexdigest()
        stored = await redis.get(key)
        assert stored is not None
        assert "Harbour Pointe" not in stored
        assert 0 < await redis.ttl(key) <= 90 * 24 * 3600
        async for other in redis.scan_iter("geocode:*"):
            assert "Harbour" not in other

    async def test_text_with_no_clear_place_never_searches(self, client: AsyncClient):
        search = AsyncMock()
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text("Disneyland Park") is None
        search.assert_not_awaited()


class TestSearchPlaces:
    """Open-Meteo's own failures become GeocodingUnavailable (Review Focus 4)."""

    async def test_a_timeout_is_unavailable(self, client: AsyncClient):
        import httpx
        import pytest

        from app.services.geocoding import search_places

        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ReadTimeout("slow"))):
            with pytest.raises(GeocodingUnavailable):
                await search_places("Mukilteo")

    async def test_a_server_error_is_unavailable(self, client: AsyncClient):
        import httpx
        import pytest

        from app.services.geocoding import search_places

        reply = httpx.Response(503, request=httpx.Request("GET", "https://x"))
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=reply)):
            with pytest.raises(GeocodingUnavailable):
                await search_places("Mukilteo")

    async def test_results_become_places(self, client: AsyncClient):
        import httpx

        from app.services.geocoding import search_places

        body = {
            "results": [
                {
                    "name": "Mukilteo", "latitude": 47.94454, "longitude": -122.30458,
                    "admin1": "Washington", "country": "United States", "country_code": "US",
                    "timezone": "America/Los_Angeles", "feature_code": "PPL",
                }
            ]
        }  # fmt: skip
        reply = httpx.Response(200, json=body, request=httpx.Request("GET", "https://x"))
        get = AsyncMock(return_value=reply)
        with patch("httpx.AsyncClient.get", get):
            places = await search_places("Muk", country_code="US")
        assert places == [_place()]
        assert get.await_args.kwargs["params"]["language"] == "en"
        assert get.await_args.kwargs["params"]["countryCode"] == "US"


class TestOnlyTheNamedTownCounts:
    """Final review I6: a result must be the town the text names, and a living one."""

    async def test_a_result_with_another_name_is_rejected(self, client: AsyncClient):
        # Open-Meteo prefix-matches: "Woodland" can return "Woodland Park" or similar.
        search = AsyncMock(return_value=[_place(name="Woodland Park", admin1="Washington")])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(_unique("Woodland, WA")) is None

    async def test_the_name_must_match_but_not_its_case(self, client: AsyncClient):
        search = AsyncMock(return_value=[_place(name="Mukilteo")])
        with patch("app.services.geocoding.search_places", search):
            place = await place_from_event_text(_unique("mukilteo, WA"))
        assert place is not None and place.name == "Mukilteo"

    async def test_historical_or_abandoned_places_are_rejected(self, client: AsyncClient):
        for code in ("PPLH", "PPLQ", "PPLW"):
            search = AsyncMock(return_value=[_place(feature_code=code)])
            with patch("app.services.geocoding.search_places", search):
                assert await place_from_event_text(_unique("Mukilteo, WA")) is None, code

    async def test_georgia_the_country_does_not_become_georgia_the_state(self, client: AsyncClient):
        # "Tbilisi, Georgia" names the state Georgia; no US town called Tbilisi.
        search = AsyncMock(return_value=[_place(name="Tifton", admin1="Georgia")])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(_unique("Tbilisi, Georgia")) is None

    async def test_a_zip_result_needs_no_name_match(self, client: AsyncClient):
        # A ZIP search returns the postal town, whose name the text need not repeat.
        search = AsyncMock(return_value=[_place(name="Everett", admin1="Washington")])
        with patch("app.services.geocoding.search_places", search):
            assert await place_from_event_text(_unique("Suite B, WA 98204")) is not None
