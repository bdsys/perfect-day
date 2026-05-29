from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_fetch_daily_uses_archive_for_old_date():
    from app.workers.open_meteo import fetch_daily

    payload = {
        "daily": {
            "time": ["2024-01-15"],
            "temperature_2m_max": [4.1],
            "temperature_2m_min": [-1.8],
            "precipitation_sum": [3.2],
            "weathercode": [71],
            "sunrise": ["2024-01-15T07:32"],
            "sunset": ["2024-01-15T16:48"],
        }
    }
    mock_resp = httpx.Response(200, json=payload)
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)) as g:
        result = await fetch_daily(40.4406, -79.9959, date(2024, 1, 15))
    assert result == {
        "date": "2024-01-15",
        "temperature_max_c": 4.1,
        "temperature_min_c": -1.8,
        "precipitation_mm": 3.2,
        "weathercode": 71,
        "condition": "moderate snow",
        "sunrise": "2024-01-15T07:32",
        "sunset": "2024-01-15T16:48",
    }
    called_url = g.call_args.args[0]
    assert "archive-api.open-meteo.com" in called_url


@pytest.mark.asyncio
async def test_fetch_daily_uses_forecast_for_recent_date():
    from app.workers.open_meteo import fetch_daily

    today = date.today()
    payload = {
        "daily": {
            "time": [today.isoformat()],
            "temperature_2m_max": [25.0],
            "temperature_2m_min": [12.0],
            "precipitation_sum": [0.0],
            "weathercode": [0],
            "sunrise": [f"{today.isoformat()}T05:42"],
            "sunset": [f"{today.isoformat()}T20:14"],
        }
    }
    mock_resp = httpx.Response(200, json=payload)
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)) as g:
        result = await fetch_daily(40.4406, -79.9959, today)
    assert result["condition"] == "clear sky"
    called_url = g.call_args.args[0]
    assert "api.open-meteo.com/v1/forecast" in called_url


@pytest.mark.asyncio
async def test_fetch_daily_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("WEATHER_ENABLED", "false")
    from app.core import config
    config.get_settings.cache_clear()

    from app.workers.open_meteo import fetch_daily
    result = await fetch_daily(40.4406, -79.9959, date(2024, 1, 15))
    assert result is None


@pytest.mark.asyncio
async def test_fetch_daily_retries_on_429():
    from app.workers.open_meteo import fetch_daily

    bad = httpx.Response(429, headers={"Retry-After": "1"})
    good_payload = {
        "daily": {
            "time": ["2024-01-15"],
            "temperature_2m_max": [4.0], "temperature_2m_min": [-2.0],
            "precipitation_sum": [0.0], "weathercode": [0],
            "sunrise": ["2024-01-15T07:32"], "sunset": ["2024-01-15T16:48"],
        }
    }
    good = httpx.Response(200, json=good_payload)
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=[bad, good])):
        result = await fetch_daily(40.4406, -79.9959, date(2024, 1, 15))
    assert result is not None
    assert result["weathercode"] == 0


@pytest.mark.asyncio
async def test_fetch_daily_returns_none_on_repeated_failure():
    from app.workers.open_meteo import fetch_daily

    err = httpx.Response(500)
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=err)):
        result = await fetch_daily(40.4406, -79.9959, date(2024, 1, 15))
    assert result is None
