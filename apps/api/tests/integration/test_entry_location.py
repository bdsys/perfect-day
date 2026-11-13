"""Per-entry place and time zone (PoC item 24).

Spec: docs/superpowers/specs/2026-11-12-per-entry-location-design.md
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.factories import make_diary, make_entry, make_user


class TestSchema:
    async def test_entry_stores_a_place_and_time_zone(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await make_diary(db_session, owner=await make_user(db_session))
        entry = await make_entry(db_session, diary)
        entry.location_name = "Mukilteo, Washington"
        entry.lat = Decimal("47.944540")
        entry.lon = Decimal("-122.304580")
        entry.location_source = "event"
        entry.tz_override = "Asia/Tokyo"
        diary.location_name = "Everett, Washington"
        await db_session.commit()
        entry_id, diary_id = entry.id, diary.id

        # Read the database itself: an unmapped attribute would otherwise just
        # live on the Python object and come back from the identity map.
        row = (
            await db_session.execute(
                text(
                    "SELECT location_name, lat, lon, location_source, tz_override"
                    " FROM entries WHERE id = :id"
                ),
                {"id": entry_id},
            )
        ).one()
        home_name = (
            await db_session.execute(
                text("SELECT location_name FROM diaries WHERE id = :id"), {"id": diary_id}
            )
        ).scalar_one()

        assert row == (
            "Mukilteo, Washington",
            Decimal("47.944540"),
            Decimal("-122.304580"),
            "event",
            "Asia/Tokyo",
        )
        assert home_name == "Everett, Washington"

    async def test_location_source_rejects_unknown_values(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await make_diary(db_session, owner=await make_user(db_session))
        entry = await make_entry(db_session, diary)
        entry.location_source = "gps"

        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()
