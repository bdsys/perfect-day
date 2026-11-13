"""City search for the place boxes on an entry and in diary settings (item 24).

Proxies Open-Meteo's geocoding search, so the browser never calls it
directly. Signed-in users only; the default per-user rate limit applies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.models import User
from app.services.geocoding import GeocodingUnavailable, search_places

router = APIRouter(tags=["geocode"])

_MAX_RESULTS = 5


class GeocodeResult(BaseModel):
    name: str
    admin1: str | None
    country: str | None
    lat: float
    lon: float
    timezone: str | None
    label: str  # "Mukilteo, Washington, United States", as the search box lists it


@router.get("/geocode", response_model=list[GeocodeResult])
async def geocode(
    q: Annotated[str, Query(min_length=2, max_length=100)],
    user: User = Depends(get_current_user),
) -> list[GeocodeResult]:
    try:
        places = await search_places(q, count=_MAX_RESULTS)
    except GeocodingUnavailable:
        raise HTTPException(status_code=503, detail="geocoding_unavailable") from None
    return [
        GeocodeResult(
            name=p.name,
            admin1=p.admin1,
            country=p.country,
            lat=p.lat,
            lon=p.lon,
            timezone=p.timezone,
            label=", ".join(part for part in (p.name, p.admin1, p.country) if part),
        )
        for p in places[:_MAX_RESULTS]
    ]
