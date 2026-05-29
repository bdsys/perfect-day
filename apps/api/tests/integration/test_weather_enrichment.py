from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Enrichment, Entry


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
        created_by="auto",
        creation_source="calendar_pick",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    @asynccontextmanager
    async def _fake_db_session():
        yield db_session

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
    ), patch(
        "app.workers.utils.db_session", _fake_db_session,
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
        created_by="auto", creation_source="calendar_pick",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    @asynccontextmanager
    async def _fake_db_session():
        yield db_session

    fake_payload = {
        "date": "2024-01-15", "temperature_max_c": 4.0, "temperature_min_c": -2.0,
        "precipitation_mm": 0.0, "weathercode": 0, "condition": "clear sky",
        "sunrise": "2024-01-15T07:32", "sunset": "2024-01-15T16:48",
    }
    with patch(
        "app.workers.open_meteo.fetch_daily", AsyncMock(return_value=fake_payload),
    ), patch(
        "app.workers.llm.generate_draft_for_entry", AsyncMock(return_value=None),
    ), patch(
        "app.workers.utils.db_session", _fake_db_session,
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
        created_by="auto", creation_source="calendar_pick",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    @asynccontextmanager
    async def _fake_db_session():
        yield db_session

    fetch_spy = AsyncMock()
    with patch("app.workers.open_meteo.fetch_daily", fetch_spy), \
         patch("app.workers.llm.generate_draft_for_entry", AsyncMock(return_value=None)), \
         patch("app.workers.utils.db_session", _fake_db_session):
        from app.workers.tasks import _generate_entry_draft
        await _generate_entry_draft(str(entry.id))

    fetch_spy.assert_not_called()
