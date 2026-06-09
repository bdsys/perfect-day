"""Open-Meteo daily weather client.

No API key required. Free tier allows ~10k requests/day per IP, more than
enough for this PoC. Uses two endpoints:

- Forecast: api.open-meteo.com/v1/forecast — recent + near-future dates.
- Archive: archive-api.open-meteo.com/v1/archive — historical, used for
  any date >= 2 days in the past. Backfill always uses archive.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
import structlog

from app.core.config import get_settings

log = structlog.get_logger()

# WMO weather interpretation codes — abbreviated mapping. Full table at
# https://open-meteo.com/en/docs#api_form. Unknown codes fall back to "unknown".
_WEATHERCODE_LABELS: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "moderate snow",
    73: "heavy snow",
    75: "snowfall",
    80: "rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

_DAILY_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "weathercode,sunrise,sunset"
)


def _is_archive_date(target: date) -> bool:
    return target <= date.today() - timedelta(days=2)


async def fetch_daily(lat: float, lon: float, target: date) -> dict[str, Any] | None:
    """Fetch a single day's weather. Returns normalized payload or None on
    permanent failure (timeout, repeated 5xx, missing data, feature disabled)."""
    settings = get_settings()
    if not settings.weather_enabled:
        return None

    base = (
        settings.open_meteo_archive_url
        if _is_archive_date(target)
        else settings.open_meteo_forecast_url
    )
    params = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
        "daily": _DAILY_VARS,
        "timezone": "auto",
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
                resp = await client.get(base, params=params)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
                    log.warning("open_meteo_rate_limited", retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(min(retry_after, 16))
                    continue

                if 500 <= resp.status_code < 600:
                    log.warning("open_meteo_server_error", status=resp.status_code, attempt=attempt)
                    if attempt == max_retries - 1:
                        return None
                    await asyncio.sleep(4**attempt)
                    continue

                if not resp.is_success:
                    log.warning("open_meteo_unexpected_status", status=resp.status_code, attempt=attempt)
                    if attempt == max_retries - 1:
                        return None
                    await asyncio.sleep(4**attempt)
                    continue
                return _normalize(resp.json(), target)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            log.warning("open_meteo_fetch_failed", error=str(exc), attempt=attempt)
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(4**attempt)

    return None


def _normalize(data: dict[str, Any], target: date) -> dict[str, Any] | None:
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    if not times or times[0] != target.isoformat():
        log.warning("open_meteo_unexpected_payload", target=target.isoformat(), times=times)
        return None
    code = (daily.get("weathercode") or [None])[0]
    return {
        "date": times[0],
        "temperature_max_c": (daily.get("temperature_2m_max") or [None])[0],
        "temperature_min_c": (daily.get("temperature_2m_min") or [None])[0],
        "precipitation_mm": (daily.get("precipitation_sum") or [None])[0],
        "weathercode": code,
        "condition": _WEATHERCODE_LABELS.get(code, "unknown") if code is not None else None,
        "sunrise": (daily.get("sunrise") or [None])[0],
        "sunset": (daily.get("sunset") or [None])[0],
    }
