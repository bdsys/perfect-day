"""Turn place names and calendar location text into places (PoC item 24).

Uses Open-Meteo's free geocoding search (built on GeoNames). It matches
cities, towns and postal codes, not street addresses or venues, and a search
must match the start of a place name:
https://open-meteo.com/en/docs/geocoding-api

Calendar location text ("Mukilteo Library, 4675 Harbour Pointe Blvd,
Mukilteo, WA") is reduced to a city or ZIP search by strict US-only rules
first. A wrong place shows wrong weather, which is worse than none, so
anything unclear gives no match. The rules and the test behind them are in
docs/superpowers/specs/2026-11-12-per-entry-location-design.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

from app.core.config import get_settings
from app.core.dependencies import get_redis

log = structlog.get_logger()

SEARCH_URL = "https://geocoding-api.open-meteo.com/v1/search"
_CACHE_PREFIX = "geocode:"
_CACHE_SECONDS = 90 * 24 * 3600

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
_STATE_BY_NAME = {name.lower(): name for name in US_STATES.values()}
_COUNTRY_SUFFIXES = {"usa", "us", "united states", "united states of america"}
# A ZIP counts only straight after a state abbreviation ("WA 98072"), so a
# house number ("12345 Main St") is never read as one.
_STATE_THEN_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
_STATE_PART = re.compile(r"([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?")


@dataclass(frozen=True)
class Place:
    name: str
    admin1: str | None
    country: str | None
    country_code: str | None
    lat: float
    lon: float
    timezone: str | None
    feature_code: str | None = None

    @property
    def label(self) -> str:
        """What an entry shows: "Mukilteo, Washington", or "Paris, France" outside the US."""
        region = self.admin1 if self.country_code == "US" else self.country
        return f"{self.name}, {region}" if region else self.name


class GeocodingUnavailable(Exception):
    """Open-Meteo could not be reached or answered with an error."""


def parse_us_location(text: str) -> tuple[Literal["zip", "city"], str, str] | None:
    """Reduce calendar location text to a search: (kind, query, state name), or None.

    No network. US only; anything unclear gives None.
    """
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if parts and parts[-1].lower() in _COUNTRY_SUFFIXES:
        parts = parts[:-1]
    if not parts:
        return None

    zip_match = _STATE_THEN_ZIP.search(text)
    if zip_match and zip_match.group(1) in US_STATES:
        return ("zip", zip_match.group(2), US_STATES[zip_match.group(1)])

    last = parts[-1]
    abbreviation = _STATE_PART.fullmatch(last)
    if abbreviation and abbreviation.group(1) in US_STATES:
        state = US_STATES[abbreviation.group(1)]
    elif last.lower() in _STATE_BY_NAME:
        state = _STATE_BY_NAME[last.lower()]
    else:
        return None

    # The city is the part just before the state. With more parts, the first
    # is the venue and is never used; "Seattle, WA" on its own is allowed.
    before_state = parts[:-1]
    if not before_state:
        return None
    city = before_state[-1]
    if re.search(r"\d", city):
        return None
    return ("city", city, state)


async def search_places(
    query: str, *, count: int = 5, country_code: str | None = None
) -> list[Place]:
    """Search Open-Meteo. Raises GeocodingUnavailable when it can't answer."""
    params: dict[str, str | int] = {"name": query, "count": count, "language": "en"}
    if country_code:
        params["countryCode"] = country_code
    timeout = get_settings().open_meteo_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(SEARCH_URL, params=params)
    except httpx.HTTPError as exc:
        raise GeocodingUnavailable(type(exc).__name__) from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise GeocodingUnavailable(f"status {response.status_code}")
    if response.status_code >= 400:
        # Open-Meteo rejects some queries (too short, bad characters): no places.
        return []
    try:
        results = response.json().get("results") or []
    except ValueError as exc:
        raise GeocodingUnavailable("not json") from exc
    return [
        Place(
            name=r["name"],
            admin1=r.get("admin1"),
            country=r.get("country"),
            country_code=r.get("country_code"),
            lat=float(r["latitude"]),
            lon=float(r["longitude"]),
            timezone=r.get("timezone"),
            feature_code=r.get("feature_code"),
        )
        for r in results
        if "name" in r and "latitude" in r and "longitude" in r
    ]


async def place_from_event_text(text: str) -> Place | None:
    """The town a calendar location names, or None. Never a guess."""
    parsed = parse_us_location(text)
    if parsed is None:
        return None
    kind, query, state = parsed

    # Keyed by a hash: the location text itself is never stored.
    key = _CACHE_PREFIX + hashlib.sha256(text.encode()).hexdigest()
    cached = await _cache_get(key)
    if cached is not None:
        return None if cached == "null" else Place(**json.loads(cached))

    try:
        results = await search_places(query, country_code="US")
    except GeocodingUnavailable as exc:
        log.warning("geocoding_unavailable", error=str(exc))
        return None  # not cached: try again next time

    place = next(
        (
            r
            for r in results
            if r.admin1 == state
            and (kind == "zip" or (r.feature_code or "").startswith("PPL"))
        ),
        None,
    )
    await _cache_set(key, "null" if place is None else json.dumps(dataclasses.asdict(place)))
    return place


async def _cache_get(key: str) -> str | None:
    try:
        return await get_redis().get(key)
    except Exception as exc:  # a cache problem must not stop the lookup
        log.warning("geocode_cache_unavailable", error=str(exc))
        return None


async def _cache_set(key: str, value: str) -> None:
    try:
        await get_redis().set(key, value, ex=_CACHE_SECONDS)
    except Exception as exc:
        log.warning("geocode_cache_unavailable", error=str(exc))
