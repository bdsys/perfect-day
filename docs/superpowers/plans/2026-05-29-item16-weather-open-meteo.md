# Item 16 — Weather Enrichment (Open-Meteo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Save canonical copy to:** `docs/superpowers/plans/2026-05-29-item16-weather-open-meteo.md` once plan mode exits.

**Goal:** Fetch daily weather from Open-Meteo for each diary entry's date(s) and persist as `Enrichment` rows so the LLM prompt can surface weather context in generated drafts. Wire into both the per-entry live path (`generate_entry_draft`) and the backfill chunk loop, with `(entry_id, kind, captured_for_at)` as the new uniqueness boundary so multi-day entries can carry per-day rows.

**Architecture:** New `app/workers/open_meteo.py` async client (no API key, separate base URLs for forecast vs. archive), new `app/workers/enrichments.py` orchestrator (lat/lon resolution → idempotency check → fetch → upsert), Alembic migration to add `Diary.lat`/`Diary.lon` columns and relax the `Enrichment` unique constraint. Per-entry hook lives at the top of `_generate_entry_draft` (Celery task already chained after rule evaluation). Backfill calls the same orchestrator at chunk-end after events are ingested.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Celery, `httpx.AsyncClient`, structlog, Pydantic v2. Tests via pytest + pytest-asyncio + testcontainers.

---

## Context

**Why this change.** Phase 2 Wave B item #16. The diary spec (`design/02-data-model.md:154-161`, `design/06-scan-worker.md:147`, `design/09-poc-scope.md:44`) calls for Open-Meteo weather to be attached to each entry as `Enrichment(kind='weather')` rows so the LLM has factual weather context (otherwise the system prompt explicitly forbids the model from inventing weather). Open-Meteo is free, key-less, and has decades of historical data — a good fit for both live and backfill paths. The `Enrichment` model and the prompt builder loop that surfaces enrichments already exist (`apps/api/app/models/__init__.py:439-454`, `apps/api/app/workers/llm.py:213-216, 238-244`); item 16 only needs to **fetch and write** rows.

**User-confirmed design decisions (2026-05-29 planning session):**

1. **Lat/lon source:** photo EXIF first, diary fallback. New `Diary.lat`/`Diary.lon` columns are added by this item; UI for setting them ships in item 19 (Diary edit settings). Until item 19, operators set lat/lon via `PATCH /v1/diaries/{id}` or admin SQL.
2. **Multi-day entries:** one weather row per day in the entry range. Drop the existing `UniqueConstraint(entry_id, kind)` and replace with `UniqueConstraint(entry_id, kind, captured_for_at)`. This is future-proof for item 15 (multi-day entries).
3. **Citation validator:** extend `validate_citation` so JSON-dumped enrichment payloads count as cited text (alongside event payloads). Prevents weather-derived prose tokens (e.g. "Saturday", "Sunny") from being flagged.
4. **Backfill scope:** wire weather call into `run_backfill` alongside the per-entry path. Both paths funnel through the same orchestrator helper for idempotency.

**Critical files:**

- `apps/api/app/models/__init__.py:272-325` (Entry, with `enrichments` relationship), `:368-395` (Diary), `:439-454` (Enrichment + UniqueConstraint to relax).
- `apps/api/app/workers/llm.py:172-246` (build_prompt — read path, no changes needed for weather to appear), `:254-292` (validate_citation — touched).
- `apps/api/app/workers/tasks.py:450-463` (`_generate_entry_draft` — hook point for live path).
- `apps/api/app/workers/backfill.py:80-114` (chunk loop — hook point for backfill path).
- `apps/api/app/workers/calendar_sync.py:99-161` (httpx retry pattern — reference for new client).
- `apps/api/app/core/config.py` (add Open-Meteo settings).
- `apps/api/app/routers/v1/diaries.py` + `apps/api/app/schemas/...` (DiaryPatch/DiaryOut — add lat/lon fields).
- `apps/api/alembic/versions/0008_regen_modes.py` (template for new migration).
- `apps/api/tests/unit/test_llm_prompt.py:86-94` (already has weather assertion — sanity check).
- `apps/api/tests/integration/test_backfill_worker.py` (template for backfill integration test).
- `apps/api/tests/unit/test_llm_validator.py` (template for citation validator tests).

**Reuse:**
- `httpx.AsyncClient` retry pattern from `app/workers/calendar_sync.py:99-161`.
- `db_session()` async context manager from `app/workers/utils.py`.
- `structlog.get_logger()` for logs.
- `EXIF parser` from `app/services/photos.py:121-123` (`parse_exif()`) — already returns lat/lon as floats.
- `_iter_week_chunks` semantics in `app/workers/backfill.py:16-32` already enumerate per-day spans; new helper `_iter_entry_dates(entry)` will mirror it for the per-entry path.

**Out of scope for this plan:**
- Diary settings UI (item 19).
- Geocoding free-form calendar location strings (deferred indefinitely).
- Hourly-resolution weather. PoC uses daily.
- Historical-vs-forecast cutover beyond simple "is the date in the past?" check.
- Tier-gating weather fetches (no tier impact in PoC).

---

## File Structure

**New files:**
- `apps/api/app/workers/open_meteo.py` — async HTTP client; pure I/O, no DB.
- `apps/api/app/workers/enrichments.py` — orchestrator: lat/lon resolution + idempotency + persistence; calls open_meteo client.
- `apps/api/alembic/versions/0009_weather_enrichment.py` — migration.
- `apps/api/tests/unit/test_open_meteo.py` — client unit tests with httpx mocked.
- `apps/api/tests/unit/test_enrichments.py` — orchestrator unit tests (lat/lon resolution, idempotency).
- `apps/api/tests/integration/test_weather_enrichment.py` — end-to-end through `_generate_entry_draft`.

**Modified files:**
- `apps/api/app/models/__init__.py` — Diary gets `lat`/`lon`; Enrichment unique constraint changes.
- `apps/api/app/core/config.py` — `weather_enabled`, `open_meteo_forecast_url`, `open_meteo_archive_url`, `open_meteo_timeout_seconds`.
- `apps/api/app/workers/tasks.py` — `_generate_entry_draft` calls enrichment orchestrator before LLM.
- `apps/api/app/workers/backfill.py` — chunk loop calls orchestrator after `ingest_calendar_event`.
- `apps/api/app/workers/llm.py` — `validate_citation` extended to include enrichment payloads in cited text; `generate_draft_for_entry` already uses `selectinload(Entry.enrichments)` so no change there.
- `apps/api/app/routers/v1/diaries.py` (and matching Pydantic schemas in `app/schemas/diaries.py`) — `DiaryPatch` / `DiaryOut` accept and surface `lat` / `lon`.
- `apps/api/.env.example` — document new env vars.
- `POC_PHASE2_TODO.md` — mark item 16 as **done** at end of plan.

---

## Tasks

### Task 1: Alembic migration — Diary lat/lon + relax Enrichment unique constraint

**Files:**
- Create: `apps/api/alembic/versions/0009_weather_enrichment.py`

- [ ] **Step 1: Write the migration**

```python
"""Weather enrichment: add Diary lat/lon, relax Enrichment uniqueness to per-day.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

This migration:
1. Adds nullable lat/lon columns to diaries (NUMERIC(9,6) — same precision as photos.lat/lon).
2. Drops the (entry_id, kind) unique constraint on enrichments.
3. Adds (entry_id, kind, captured_for_at) unique constraint to support per-day weather rows
   for multi-day entries.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diaries", sa.Column("lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("diaries", sa.Column("lon", sa.Numeric(9, 6), nullable=True))

    op.drop_constraint("uq_enrichments_entry_kind", "enrichments", type_="unique")
    op.create_unique_constraint(
        "uq_enrichments_entry_kind_captured",
        "enrichments",
        ["entry_id", "kind", "captured_for_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_enrichments_entry_kind_captured", "enrichments", type_="unique")
    op.create_unique_constraint(
        "uq_enrichments_entry_kind",
        "enrichments",
        ["entry_id", "kind"],
    )
    op.drop_column("diaries", "lon")
    op.drop_column("diaries", "lat")
```

- [ ] **Step 2: Update SQLAlchemy models to match**

In `apps/api/app/models/__init__.py`:
- Add to the `Diary` class:

```python
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
```

- Replace `Enrichment.__table_args__` line 454:

```python
    __table_args__ = (
        UniqueConstraint(
            "entry_id", "kind", "captured_for_at",
            name="uq_enrichments_entry_kind_captured",
        ),
    )
```

(`Decimal` is already imported via `from decimal import Decimal` near top of file — verify; if not, add it.)

- [ ] **Step 3: Run migration upgrade and downgrade locally**

Run:
```bash
cd apps/api && alembic upgrade head
cd apps/api && alembic downgrade 0008
cd apps/api && alembic upgrade head
```
Expected: each command exits 0; `\d diaries` in psql shows lat/lon columns; `\d enrichments` shows new constraint name.

- [ ] **Step 4: Commit**

```bash
git add apps/api/alembic/versions/0009_weather_enrichment.py apps/api/app/models/__init__.py
git commit -m "feat(api): add Diary lat/lon and relax Enrichment uniqueness for multi-day weather (item 16)"
```

---

### Task 2: Config — Open-Meteo settings

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/.env.example`

- [ ] **Step 1: Write the failing config test**

Create `apps/api/tests/unit/test_config.py` addition (or new test file if simpler):

```python
def test_settings_have_open_meteo_defaults():
    from app.core.config import get_settings
    s = get_settings()
    assert s.weather_enabled is True
    assert s.open_meteo_forecast_url == "https://api.open-meteo.com/v1/forecast"
    assert s.open_meteo_archive_url == "https://archive-api.open-meteo.com/v1/archive"
    assert s.open_meteo_timeout_seconds == 30
```

Run: `cd apps/api && pytest tests/unit/test_config.py::test_settings_have_open_meteo_defaults -v`
Expected: FAIL — attributes missing.

- [ ] **Step 2: Add fields to Settings**

In `apps/api/app/core/config.py` (inside the `Settings` class):

```python
    weather_enabled: bool = True
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_timeout_seconds: int = 30
```

- [ ] **Step 3: Add documentation lines to `.env.example`**

```
# Weather enrichment (Open-Meteo, no API key required). Set WEATHER_ENABLED=false to disable.
WEATHER_ENABLED=true
OPEN_METEO_FORECAST_URL=https://api.open-meteo.com/v1/forecast
OPEN_METEO_ARCHIVE_URL=https://archive-api.open-meteo.com/v1/archive
OPEN_METEO_TIMEOUT_SECONDS=30
```

- [ ] **Step 4: Run config test**

Run: `cd apps/api && pytest tests/unit/test_config.py::test_settings_have_open_meteo_defaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/core/config.py apps/api/.env.example apps/api/tests/unit/test_config.py
git commit -m "feat(api): add Open-Meteo settings (item 16)"
```

---

### Task 3: Open-Meteo client (`workers/open_meteo.py`)

**Files:**
- Create: `apps/api/app/workers/open_meteo.py`
- Test: `apps/api/tests/unit/test_open_meteo.py`

The client has one public coroutine `fetch_daily(lat, lon, target_date)` that returns a normalized payload `dict` for one day or `None` if the date is unfetchable. Decides forecast-vs-archive based on whether `target_date` is in the past (>=2 days old → archive, otherwise forecast). Daily variables we request: `temperature_2m_max`, `temperature_2m_min`, `precipitation_sum`, `weathercode`, `sunrise`, `sunset`. Timezone is fixed to `auto` so Open-Meteo localizes to lat/lon.

Schema returned (the `payload` we'll persist into `enrichments.payload`):

```json
{
  "date": "2026-05-29",
  "temperature_max_c": 22.4,
  "temperature_min_c": 11.7,
  "precipitation_mm": 0.0,
  "weathercode": 1,
  "condition": "mainly clear",
  "sunrise": "2026-05-29T05:42",
  "sunset":  "2026-05-29T20:14"
}
```

`weathercode` mapping table (WMO codes — Open-Meteo standard) lives in the same module as a private constant; tests assert a few representative mappings.

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/unit/test_open_meteo.py
from datetime import date, timedelta
from unittest.mock import patch, AsyncMock

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
    from app.workers.open_meteo import fetch_daily

    monkeypatch.setenv("WEATHER_ENABLED", "false")
    from app.core.config import _clear_settings_cache
    _clear_settings_cache()

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
```

Run: `cd apps/api && pytest tests/unit/test_open_meteo.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 2: Implement the client**

```python
# apps/api/app/workers/open_meteo.py
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

    base = settings.open_meteo_archive_url if _is_archive_date(target) else settings.open_meteo_forecast_url
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

                resp.raise_for_status()
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
```

- [ ] **Step 3: Run tests**

Run: `cd apps/api && pytest tests/unit/test_open_meteo.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workers/open_meteo.py apps/api/tests/unit/test_open_meteo.py
git commit -m "feat(api): Open-Meteo daily weather client (item 16)"
```

---

### Task 4: Enrichment orchestrator (`workers/enrichments.py`)

**Files:**
- Create: `apps/api/app/workers/enrichments.py`
- Test: `apps/api/tests/unit/test_enrichments.py`

**Responsibilities:**
1. Resolve lat/lon for an Entry: photos with EXIF GPS first, diary fallback.
2. For each date in the entry's range (single day if `entry_end_date is None`), check whether an `Enrichment(entry_id, kind='weather', captured_for_at=<date>)` already exists; skip fetch if so.
3. Otherwise call `open_meteo.fetch_daily(...)`; if it returns a payload, INSERT a row with `kind='weather'`, `source='open_meteo'`, `captured_for_at` = midnight-UTC of the date, `fetched_at` = now.
4. Return a count `(inserted, skipped, failed)` for logging.

**Public function signature:**

```python
async def enrich_entry_weather(entry_id: uuid.UUID, db: AsyncSession) -> tuple[int, int, int]:
    """Returns (inserted, skipped_existing, failed_or_skipped_no_location)."""
```

The caller passes its own `db` so tests and orchestrators can share a transaction.

**Helper:** `_resolve_lat_lon(entry, db)`:
- Try `entry.photos` (already eagerly loaded by the Celery task) — return the first one whose `.lat is not None and .lon is not None`.
- Else load the diary, return `(diary.lat, diary.lon)`.
- Else return `None`.

**Helper:** `_iter_entry_dates(entry)`:
- If `entry.entry_end_date is None`, yield `entry.entry_date`.
- Else yield each date from `entry.entry_date` through `entry.entry_end_date` inclusive (cap at 30 days as a safety bound — log warning past that).

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/unit/test_enrichments.py
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch
from decimal import Decimal

import pytest


def _fake_entry(entry_date: date, end_date=None, photo_lat=None, photo_lon=None,
                diary_lat=None, diary_lon=None):
    """Returns a SimpleNamespace-style stub matching attribute access used by orchestrator."""
    from types import SimpleNamespace
    photos = []
    if photo_lat is not None:
        photos.append(SimpleNamespace(lat=Decimal(str(photo_lat)), lon=Decimal(str(photo_lon))))
    diary = SimpleNamespace(
        id=uuid.uuid4(),
        lat=Decimal(str(diary_lat)) if diary_lat is not None else None,
        lon=Decimal(str(diary_lon)) if diary_lon is not None else None,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        entry_date=entry_date,
        entry_end_date=end_date,
        photos=photos,
        diary_id=diary.id,
        diary=diary,
    )


def test_iter_entry_dates_single():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 5, 29))
    assert list(_iter_entry_dates(e)) == [date(2026, 5, 29)]


def test_iter_entry_dates_range():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 5, 29), end_date=date(2026, 5, 31))
    assert list(_iter_entry_dates(e)) == [date(2026, 5, 29), date(2026, 5, 30), date(2026, 5, 31)]


def test_iter_entry_dates_cap_at_30_days():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 1, 1), end_date=date(2026, 12, 31))
    out = list(_iter_entry_dates(e))
    assert len(out) == 30


def test_resolve_lat_lon_prefers_photo_exif():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29), photo_lat=10.5, photo_lon=20.5,
                    diary_lat=40.0, diary_lon=-80.0)
    result = _resolve_lat_lon(e)
    assert result == (10.5, 20.5)


def test_resolve_lat_lon_falls_back_to_diary():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29), diary_lat=40.0, diary_lon=-80.0)
    assert _resolve_lat_lon(e) == (40.0, -80.0)


def test_resolve_lat_lon_returns_none_when_unset():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29))
    assert _resolve_lat_lon(e) is None
```

Run: `cd apps/api && pytest tests/unit/test_enrichments.py -v`
Expected: FAIL — module missing.

- [ ] **Step 2: Implement orchestrator**

```python
# apps/api/app/workers/enrichments.py
"""Enrichment orchestrator: lat/lon resolution + idempotent weather fetch."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Diary, Enrichment, Entry, Photo
from app.workers import open_meteo

log = structlog.get_logger()

_MAX_RANGE_DAYS = 30


def _iter_entry_dates(entry) -> list[date]:
    if entry.entry_end_date is None:
        return [entry.entry_date]
    span = (entry.entry_end_date - entry.entry_date).days + 1
    capped = min(span, _MAX_RANGE_DAYS)
    if span > _MAX_RANGE_DAYS:
        log.warning(
            "enrichment_date_range_capped",
            entry_id=str(entry.id),
            requested_days=span,
            capped_days=capped,
        )
    return [entry.entry_date + timedelta(days=i) for i in range(capped)]


def _resolve_lat_lon(entry) -> tuple[float, float] | None:
    for photo in getattr(entry, "photos", []) or []:
        if photo.lat is not None and photo.lon is not None:
            return float(photo.lat), float(photo.lon)
    diary = getattr(entry, "diary", None)
    if diary is not None and diary.lat is not None and diary.lon is not None:
        return float(diary.lat), float(diary.lon)
    return None


async def _existing_dates(
    db: AsyncSession, entry_id: uuid.UUID
) -> set[date]:
    rows = (
        await db.execute(
            select(Enrichment.captured_for_at).where(
                Enrichment.entry_id == entry_id,
                Enrichment.kind == "weather",
            )
        )
    ).scalars().all()
    return {r.date() for r in rows if r is not None}


async def enrich_entry_weather(
    entry_id: uuid.UUID, db: AsyncSession
) -> tuple[int, int, int]:
    """Fetch weather for each date of entry's range. Returns
    (inserted, skipped_existing, failed_or_no_location)."""
    entry = (
        await db.execute(
            select(Entry)
            .options(selectinload(Entry.photos), selectinload(Entry.diary))
            .where(Entry.id == entry_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        return 0, 0, 0

    coords = _resolve_lat_lon(entry)
    if coords is None:
        log.info("enrichment_skipped_no_location", entry_id=str(entry_id))
        return 0, 0, 1
    lat, lon = coords

    target_dates = _iter_entry_dates(entry)
    already = await _existing_dates(db, entry_id)

    inserted = skipped = failed = 0
    for d in target_dates:
        if d in already:
            skipped += 1
            continue
        payload = await open_meteo.fetch_daily(lat, lon, d)
        if payload is None:
            failed += 1
            continue
        captured_for_at = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        stmt = (
            insert(Enrichment)
            .values(
                entry_id=entry_id,
                kind="weather",
                payload=payload,
                source="open_meteo",
                captured_for_at=captured_for_at,
                fetched_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                constraint="uq_enrichments_entry_kind_captured"
            )
        )
        result = await db.execute(stmt)
        if result.rowcount:
            inserted += 1
        else:
            skipped += 1
    await db.commit()
    log.info(
        "enrichment_weather_done",
        entry_id=str(entry_id),
        inserted=inserted,
        skipped=skipped,
        failed=failed,
    )
    return inserted, skipped, failed
```

(Note on imports: `Photo` is imported only because some entries' relationship returns it — if `entry.photos` is actually `EntryPhoto`, swap accordingly. Verify against `models/__init__.py` Entry relationships when implementing.)

- [ ] **Step 3: Run unit tests**

Run: `cd apps/api && pytest tests/unit/test_enrichments.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workers/enrichments.py apps/api/tests/unit/test_enrichments.py
git commit -m "feat(api): enrichment orchestrator with photo/diary lat/lon fallback (item 16)"
```

---

### Task 5: Wire orchestrator into live draft generation

**Files:**
- Modify: `apps/api/app/workers/tasks.py:460-463` (`_generate_entry_draft`)
- Test: `apps/api/tests/integration/test_weather_enrichment.py`

The hook runs **before** the LLM call so that `selectinload(Entry.enrichments)` inside `generate_draft_for_entry` picks up the freshly-inserted weather rows.

- [ ] **Step 1: Write failing integration test**

```python
# apps/api/tests/integration/test_weather_enrichment.py
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Diary, Enrichment, Entry


@pytest.mark.asyncio
async def test_generate_entry_draft_writes_weather_for_diary_lat_lon(
    db_session, make_user, make_diary_for_user
):
    user = await make_user()
    diary = await make_diary_for_user(user, lat=40.4406, lon=-79.9959)
    entry = Entry(
        diary_id=diary.id,
        entry_date=date(2024, 1, 15),
        title="Test",
        body_markdown="",
        status="draft",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    fake_payload = {
        "date": "2024-01-15",
        "temperature_max_c": 4.0, "temperature_min_c": -2.0,
        "precipitation_mm": 0.0, "weathercode": 0,
        "condition": "clear sky",
        "sunrise": "2024-01-15T07:32", "sunset": "2024-01-15T16:48",
    }
    with patch(
        "app.workers.open_meteo.fetch_daily",
        AsyncMock(return_value=fake_payload),
    ), patch(
        "app.workers.llm.generate_draft_for_entry",
        AsyncMock(return_value=None),
    ):
        from app.workers.tasks import _generate_entry_draft
        await _generate_entry_draft(str(entry.id))

    rows = (
        await db_session.execute(
            select(Enrichment).where(Enrichment.entry_id == entry.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "weather"
    assert rows[0].source == "open_meteo"
    assert rows[0].payload["temperature_max_c"] == 4.0


@pytest.mark.asyncio
async def test_generate_entry_draft_idempotent_no_dup_rows(
    db_session, make_user, make_diary_for_user
):
    user = await make_user()
    diary = await make_diary_for_user(user, lat=40.4406, lon=-79.9959)
    entry = Entry(
        diary_id=diary.id,
        entry_date=date(2024, 1, 15),
        title="T", body_markdown="", status="draft",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    fake_payload = {
        "date": "2024-01-15", "temperature_max_c": 4.0, "temperature_min_c": -2.0,
        "precipitation_mm": 0.0, "weathercode": 0, "condition": "clear sky",
        "sunrise": "2024-01-15T07:32", "sunset": "2024-01-15T16:48",
    }
    with patch(
        "app.workers.open_meteo.fetch_daily", AsyncMock(return_value=fake_payload),
    ), patch(
        "app.workers.llm.generate_draft_for_entry", AsyncMock(return_value=None),
    ):
        from app.workers.tasks import _generate_entry_draft
        await _generate_entry_draft(str(entry.id))
        await _generate_entry_draft(str(entry.id))

    rows = (
        await db_session.execute(
            select(Enrichment).where(Enrichment.entry_id == entry.id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_generate_entry_draft_no_location_no_enrichment(
    db_session, make_user, make_diary_for_user
):
    user = await make_user()
    diary = await make_diary_for_user(user)  # no lat/lon
    entry = Entry(
        diary_id=diary.id, entry_date=date(2024, 1, 15),
        title="T", body_markdown="", status="draft",
    )
    db_session.add(entry); await db_session.commit(); await db_session.refresh(entry)

    fetch_spy = AsyncMock()
    with patch("app.workers.open_meteo.fetch_daily", fetch_spy), \
         patch("app.workers.llm.generate_draft_for_entry", AsyncMock(return_value=None)):
        from app.workers.tasks import _generate_entry_draft
        await _generate_entry_draft(str(entry.id))

    fetch_spy.assert_not_called()
```

Add helper `make_diary_for_user` to `tests/integration/conftest.py` if not present:

```python
@pytest_asyncio.fixture
async def make_diary_for_user(db_session):
    async def _mk(user, lat=None, lon=None, timezone="America/Chicago"):
        from app.models import Diary
        d = Diary(owner_user_id=user.id, name="d", timezone=timezone, lat=lat, lon=lon)
        db_session.add(d); await db_session.commit(); await db_session.refresh(d)
        return d
    return _mk
```

Run: `cd apps/api && pytest tests/integration/test_weather_enrichment.py -v`
Expected: FAIL — wiring doesn't exist.

- [ ] **Step 2: Modify `_generate_entry_draft`**

In `apps/api/app/workers/tasks.py`, replace lines 460-463 with:

```python
async def _generate_entry_draft(entry_id_str: str) -> None:
    from app.workers.enrichments import enrich_entry_weather
    from app.workers.llm import generate_draft_for_entry
    from app.workers.utils import db_session

    entry_uuid = uuid.UUID(entry_id_str)
    try:
        async with db_session() as db:
            await enrich_entry_weather(entry_uuid, db)
    except Exception as exc:  # never block draft on weather failure
        log.warning(
            "enrichment_weather_skipped_due_to_error",
            entry_id=entry_id_str,
            error=str(exc),
        )
    await generate_draft_for_entry(entry_uuid)
```

(Verify `log` is in scope at top of `tasks.py`; if not, add `import structlog; log = structlog.get_logger()` near other module-level imports.)

- [ ] **Step 3: Run integration test**

Run: `cd apps/api && pytest tests/integration/test_weather_enrichment.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workers/tasks.py apps/api/tests/integration/test_weather_enrichment.py apps/api/tests/integration/conftest.py
git commit -m "feat(api): fetch weather before LLM draft generation (item 16)"
```

---

### Task 6: Wire orchestrator into backfill chunk loop

**Files:**
- Modify: `apps/api/app/workers/backfill.py:80-114`
- Test: `apps/api/tests/integration/test_backfill_worker.py` (extend existing)

After events are ingested for a chunk, call `enrich_entry_weather` for any entry that was created/updated during this chunk. The existing code already collects these in `entry_ids: set[str]`. After the chunk's `await asyncio.sleep(2)`, iterate `entry_ids` collected so far and call the orchestrator. Use a separate db session per call so a single failure doesn't poison the chunk.

- [ ] **Step 1: Write failing test**

Add to `tests/integration/test_backfill_worker.py`:

```python
@pytest.mark.asyncio
async def test_backfill_writes_weather_per_entry(
    db_session, make_user, make_diary_for_user, monkeypatch
):
    user = await make_user()
    diary = await make_diary_for_user(user, lat=40.4406, lon=-79.9959)
    # ... existing scaffold to set up BackfillRun + a couple of fake events
    fake_payload = {
        "date": "2024-01-15", "temperature_max_c": 4.0, "temperature_min_c": -2.0,
        "precipitation_mm": 0.0, "weathercode": 0, "condition": "clear sky",
        "sunrise": "2024-01-15T07:32", "sunset": "2024-01-15T16:48",
    }
    fetch_spy = AsyncMock(return_value=fake_payload)
    with patch("app.workers.open_meteo.fetch_daily", fetch_spy):
        # call run_backfill with a 1-week date range as in existing tests
        from app.workers.backfill import run_backfill
        await run_backfill(
            backfill_run_id=run.id, diary_id=diary.id,
            from_date=date(2024, 1, 14), to_date=date(2024, 1, 16),
            access_token="test", diary_timezone="America/Chicago",
        )

    rows = (
        await db_session.execute(
            select(Enrichment).where(Enrichment.kind == "weather")
        )
    ).scalars().all()
    assert len(rows) >= 1
    assert all(r.source == "open_meteo" for r in rows)
```

Run: `cd apps/api && pytest tests/integration/test_backfill_worker.py -k test_backfill_writes_weather_per_entry -v`
Expected: FAIL.

- [ ] **Step 2: Modify `run_backfill`**

In `apps/api/app/workers/backfill.py` replace the chunk-loop body around line 102-113 with:

```python
            chunk_entry_ids: set[str] = set()
            for calendar_id in calendar_ids:
                events = await _fetch_events_range(calendar_id, headers, time_min, time_max)
                total_events += len(events)
                for event in events:
                    if event.get("status") == "cancelled":
                        continue
                    result = _tasks.ingest_calendar_event.delay(event, str(diary_id), diary_timezone)
                    if result:
                        entry_ids.add(str(result))
                        chunk_entry_ids.add(str(result))

            # TODO(item-15): call group_events_into_entries for this chunk once it exists.

            # Item 16: weather enrichment per entry created/updated this chunk.
            from app.workers.enrichments import enrich_entry_weather
            for eid in chunk_entry_ids:
                try:
                    async with db_session() as db:
                        await enrich_entry_weather(uuid.UUID(eid), db)
                except Exception as exc:
                    log.warning("backfill_weather_failed", entry_id=eid, error=str(exc))

            await asyncio.sleep(2)
```

- [ ] **Step 3: Run test**

Run: `cd apps/api && pytest tests/integration/test_backfill_worker.py -k test_backfill_writes_weather_per_entry -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workers/backfill.py apps/api/tests/integration/test_backfill_worker.py
git commit -m "feat(api): wire weather enrichment into backfill chunks (item 16)"
```

---

### Task 7: Citation validator — include enrichment payloads

**Files:**
- Modify: `apps/api/app/workers/llm.py:254-292` (`validate_citation`)
- Modify: caller `generate_draft_for_entry` to pass enrichments through.
- Test: `apps/api/tests/unit/test_llm_validator.py`

The validator currently builds `cited_event_texts` from event payloads only (line 278-280). Extend to also concatenate JSON-dumped enrichment payloads, so weather words present in `payload` (e.g. `"sunrise": "..."`) reach the cited-text concatenation. The validator's signature changes to add `enrichments` parameter (nullable for backward compatibility).

- [ ] **Step 1: Write failing test**

Add to `apps/api/tests/unit/test_llm_validator.py`:

```python
def test_validate_citation_allows_words_present_in_enrichment_payload():
    from types import SimpleNamespace
    from app.workers.llm import validate_citation

    events = [SimpleNamespace(payload={"summary": "Park visit"})]
    enrichments = [SimpleNamespace(payload={
        "date": "2024-01-15", "condition": "clear sky", "sunrise": "07:32",
    })]
    output = {
        "body_markdown": "It was a clear sky morning at the park.",
        "title": "Park morning",
        "facts_used": [1],
        "title_facts_used": [1],
    }
    ok, msg, flagged = validate_citation(output, events, mode="events", enrichments=enrichments)
    # "Park" is in event payload; "It" "Sky" etc. are not capitalized except "It"
    # which is the only capitalized token and IS in events. So flagged should be empty.
    assert ok is True, msg
    assert flagged == []


def test_validate_citation_still_flags_unrelated_capitalized_tokens():
    from types import SimpleNamespace
    from app.workers.llm import validate_citation

    events = [SimpleNamespace(payload={"summary": "Park visit"})]
    enrichments = [SimpleNamespace(payload={"condition": "clear sky"})]
    output = {
        "body_markdown": "We saw Aunt Mildred at the park.",
        "title": "T",
        "facts_used": [1],
        "title_facts_used": [1],
    }
    ok, msg, flagged = validate_citation(output, events, mode="events", enrichments=enrichments)
    assert "Mildred" in flagged or "Aunt" in flagged
```

Run: `cd apps/api && pytest tests/unit/test_llm_validator.py -k enrichment -v`
Expected: FAIL — validator signature lacks `enrichments`.

- [ ] **Step 2: Modify validator**

In `apps/api/app/workers/llm.py`, change function signature and the cited-text concatenation:

```python
def validate_citation(
    output: dict, events: list, mode: str = "events", body_seed: str = "",
    enrichments: list | None = None,
) -> tuple[bool, str, list[str]]:
    if mode == "polish":
        return True, "", []

    facts_used = output.get("facts_used", [])
    title_facts = output.get("title_facts_used", [])
    max_idx = len(events)

    for idx in facts_used + title_facts:
        if not isinstance(idx, int) or idx < 1 or idx > max_idx:
            return False, f"facts_used contains invalid event index: {idx}", []

    if mode == "hybrid":
        return True, "", []

    body = output.get("body_markdown", "")
    title = output.get("title", "")

    cited_event_texts = " ".join(
        json.dumps(events[i - 1].payload) for i in facts_used if 1 <= i <= max_idx
    )
    enrichment_texts = " ".join(
        json.dumps(e.payload) for e in (enrichments or [])
    )
    cited_text = cited_event_texts + " " + enrichment_texts

    tokens = re.findall(r"\b[A-Z][a-z]{2,}\b", body + " " + title)
    flagged = []
    _CALENDAR_WORDS = {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    }
    for token in tokens:
        if token not in cited_text and token not in _CALENDAR_WORDS:
            flagged.append(token)
    if flagged:
        return False, f"flagged tokens: {flagged}", flagged
    return True, "", []
```

- [ ] **Step 3: Update the caller**

Inside `generate_draft_for_entry` (around the `validate_citation(...)` call), pass `enrichments=entry.enrichments`. Search for `validate_citation(` in `llm.py`; replace each call site to include the new kwarg.

- [ ] **Step 4: Run validator tests + existing prompt/validator tests**

Run: `cd apps/api && pytest tests/unit/test_llm_validator.py tests/unit/test_llm_prompt.py -v`
Expected: PASS — including the existing test at `test_llm_prompt.py:86-94`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/llm.py apps/api/tests/unit/test_llm_validator.py
git commit -m "feat(api): include enrichment payloads in citation validation (item 16)"
```

---

### Task 8: Diary lat/lon API surface

**Files:**
- Modify: `apps/api/app/routers/v1/diaries.py` and `apps/api/app/schemas/diaries.py` (or wherever `DiaryPatch` / `DiaryOut` live — confirm with `grep -rn "class DiaryPatch" apps/api/app`).
- Test: `apps/api/tests/integration/test_diaries.py` — add cases.

Add `lat: Optional[Decimal]` and `lon: Optional[Decimal]` to `DiaryPatch` and `DiaryOut`. Range validation: `-90 <= lat <= 90`, `-180 <= lon <= 180`. Patch handler must include them in the update set.

- [ ] **Step 1: Failing API test**

```python
@pytest.mark.asyncio
async def test_patch_diary_sets_lat_lon(client, auth_headers, make_diary):
    diary = await make_diary()
    resp = await client.patch(
        f"/v1/diaries/{diary.id}",
        json={"lat": 40.4406, "lon": -79.9959},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert float(body["lat"]) == 40.4406
    assert float(body["lon"]) == -79.9959


@pytest.mark.asyncio
async def test_patch_diary_rejects_out_of_range_lat(client, auth_headers, make_diary):
    diary = await make_diary()
    resp = await client.patch(
        f"/v1/diaries/{diary.id}",
        json={"lat": 999.0, "lon": 0.0},
        headers=auth_headers,
    )
    assert resp.status_code == 422
```

Run: `cd apps/api && pytest tests/integration/test_diaries.py -k lat_lon -v`
Expected: FAIL.

- [ ] **Step 2: Add fields to schemas + patch handler**

```python
# in DiaryPatch
    lat: Annotated[Decimal | None, Field(ge=-90, le=90)] = None
    lon: Annotated[Decimal | None, Field(ge=-180, le=180)] = None
# in DiaryOut
    lat: Decimal | None = None
    lon: Decimal | None = None
```

Update the `PATCH` handler in `diaries.py` to copy `payload.lat`/`payload.lon` onto the diary when present, treating `None` as "leave unchanged" (use `model_dump(exclude_unset=True)`).

- [ ] **Step 3: Run tests**

Run: `cd apps/api && pytest tests/integration/test_diaries.py -v`
Expected: PASS (including new lat/lon cases).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/v1/diaries.py apps/api/app/schemas/diaries.py apps/api/tests/integration/test_diaries.py
git commit -m "feat(api): expose Diary lat/lon via DiaryPatch and DiaryOut (item 16)"
```

---

### Task 9: Run full backend test suite + smoke test live LLM path

**Files:** none.

- [ ] **Step 1: Run all backend tests**

Run: `cd apps/api && pytest -x -q`
Expected: 0 failures. If any unit/integration test now references the old `uq_enrichments_entry_kind` constraint name, fix it.

- [ ] **Step 2: Run repo-wide test suite**

Run: `make test-all`
Expected: pass per project CLAUDE.md instruction.

- [ ] **Step 3: End-to-end smoke (manual, with LLM keys configured)**

```bash
# Start stack
docker compose up -d
cd apps/api && alembic upgrade head

# Seed: create a user, diary, set lat/lon via PATCH, create an entry
# Use existing seed scripts or psql. Trigger a draft generation via the
# existing endpoint or by inserting a row that fires the rule engine.
```

Verify via psql:
```sql
SELECT entry_id, captured_for_at, payload->>'condition' AS cond
FROM enrichments WHERE kind = 'weather' ORDER BY fetched_at DESC LIMIT 5;
```
Expected: rows present, `cond` populated.

Verify the generated entry's body via the web UI: weather should be reflected in narrative when LLM chose to use it. `flagged_tokens` column on `entries` should be empty for typical weather-mentioning prose.

- [ ] **Step 4: Commit any test fix-ups, update tracker**

In `POC_PHASE2_TODO.md`, change line 24 from `pending` to `**done**`. Add a per-item-scaffold update on line 59 to reflect what landed.

```bash
git add POC_PHASE2_TODO.md
git commit -m "docs: mark item 16 (weather enrichment) as done"
```

---

## Verification

**Unit tests:**
- `pytest apps/api/tests/unit/test_open_meteo.py -v` — client mocked HTTP behavior.
- `pytest apps/api/tests/unit/test_enrichments.py -v` — orchestrator helpers.
- `pytest apps/api/tests/unit/test_llm_validator.py -v` — validator with enrichments.
- `pytest apps/api/tests/unit/test_llm_prompt.py -v` — existing weather assertion still passes.

**Integration tests:**
- `pytest apps/api/tests/integration/test_weather_enrichment.py -v` — live path: weather written before draft.
- `pytest apps/api/tests/integration/test_backfill_worker.py -v` — backfill chunks write weather.
- `pytest apps/api/tests/integration/test_diaries.py -v` — PATCH lat/lon.

**Migration:**
- `cd apps/api && alembic upgrade head` then `alembic downgrade 0008` then `alembic upgrade head` — all clean.

**Manual end-to-end:**
- `make test-all` — all green.
- Stack up, set diary lat/lon, fire draft generation, inspect `enrichments` table and entry body via web UI.

**Live API smoke (optional):**
- One real call: `python -c "import asyncio; from app.workers.open_meteo import fetch_daily; from datetime import date; print(asyncio.run(fetch_daily(40.4406, -79.9959, date(2024, 1, 15))))"` — expect a non-None payload.

---

## Out-of-Scope follow-ups (track separately)

1. Diary settings UI for lat/lon (item 19).
2. Geocoding free-form calendar event location strings.
3. Hourly weather resolution.
4. Forecast→archive cutover refinement (currently 2-day boundary; could be smarter).
5. Per-tier weather throttling.
6. Spotify-style enrichment plumbing (similar shape, different source — design decisions land in their own item).
