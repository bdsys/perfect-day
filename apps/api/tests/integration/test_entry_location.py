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


# ---------------------------------------------------------------------------
# Task 3: where an entry's place comes from, and weather following it
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402
from datetime import UTC, date, datetime  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models import Enrichment, Entry, User  # noqa: E402
from app.services.geocoding import GeocodingUnavailable, Place  # noqa: E402
from tests.fixtures.factories import make_entry_photo, make_event, make_photo  # noqa: E402

MUKILTEO = Place(
    name="Mukilteo", admin1="Washington", country="United States", country_code="US",
    lat=47.94454, lon=-122.30458, timezone="America/Los_Angeles", feature_code="PPL",
)  # fmt: skip
HOME = (47.978985, -122.202079)  # Everett
PHOTO = (47.606209, -122.332069)  # Seattle
WEATHER = {
    "date": "2026-05-16", "temperature_max_c": 18.0, "temperature_min_c": 9.0,
    "precipitation_mm": 0.0, "weathercode": 1, "condition": "mainly clear",
    "sunrise": "2026-05-16T05:30", "sunset": "2026-05-16T20:40",
}  # fmt: skip


def _worker_uses(db_session):
    """Point the worker's own sessions at the test session."""

    @asynccontextmanager
    async def _session():
        yield db_session

    return patch("app.workers.utils.db_session", _session)


async def _entry_in_diary(db_session, *, home: tuple[float, float] | None = None):
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner=owner)
    if home:
        diary.lat, diary.lon = home
        diary.location_name = "Everett, Washington"
    entry = await make_entry(db_session, diary, entry_date=date(2026, 5, 16))
    await db_session.commit()
    return owner, diary, entry


async def _refresh(db_session, entry, *, geocode=None, weather=None, refetch_weather=False):
    from app.workers.tasks import _refresh_entry_location

    geocode = geocode or AsyncMock(return_value=None)
    weather = weather or AsyncMock(return_value=WEATHER)
    entry_id = entry.id
    with (
        _worker_uses(db_session),
        patch("app.services.entry_location.place_from_event_text", geocode),
        patch("app.workers.open_meteo.fetch_daily", weather),
    ):
        await _refresh_entry_location(str(entry_id), refetch_weather=refetch_weather)
    return await _reload(db_session, entry_id)


async def _reload(db_session, entry_id):
    db_session.expire_all()
    return (await db_session.execute(select(Entry).where(Entry.id == entry_id))).scalar_one()


def _coords(entry) -> tuple[float, float] | None:
    if entry.lat is None:
        return None
    return (round(float(entry.lat), 5), round(float(entry.lon), 5))


class TestWhereThePlaceComesFrom:
    async def test_photo_gps_beats_event_text(self, client, db_session):
        owner, _, entry = await _entry_in_diary(db_session, home=HOME)
        await make_event(db_session, entry=entry, payload={"summary": "Story time",
                         "location": "Mukilteo Library, Mukilteo, WA"})  # fmt: skip
        photo = await make_photo(db_session, user=owner, lat=PHOTO[0], lon=PHOTO[1])
        await make_entry_photo(db_session, entry=entry, photo=photo)
        await db_session.commit()

        stored = await _refresh(db_session, entry, geocode=AsyncMock(return_value=MUKILTEO))

        assert stored.location_source == "photo"
        assert stored.location_name is None
        assert _coords(stored) == (round(PHOTO[0], 5), round(PHOTO[1], 5))

    async def test_event_text_used_when_no_photo_gps(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        await make_event(db_session, entry=entry, payload={"summary": "Story time",
                         "location": "Mukilteo Library, Mukilteo, WA"})  # fmt: skip
        await db_session.commit()

        stored = await _refresh(db_session, entry, geocode=AsyncMock(return_value=MUKILTEO))

        assert stored.location_source == "event"
        assert stored.location_name == "Mukilteo, Washington"
        assert _coords(stored) == (47.94454, -122.30458)

    async def test_home_place_used_when_nothing_else(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)

        stored = await _refresh(db_session, entry)

        assert stored.location_source == "home"
        assert stored.location_name == "Everett, Washington"
        assert _coords(stored) == (round(HOME[0], 5), round(HOME[1], 5))

    async def test_no_place_when_nothing_resolves(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session)
        await make_event(db_session, entry=entry, payload={"summary": "x", "location": "Home"})
        await db_session.commit()

        stored = await _refresh(db_session, entry)

        assert (stored.location_name, stored.lat, stored.lon, stored.location_source) == (
            None, None, None, None,
        )  # fmt: skip

    async def test_geocoder_down_falls_back_to_home(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        await make_event(db_session, entry=entry, payload={"summary": "x",
                         "location": "Mukilteo Library, Mukilteo, WA"})  # fmt: skip
        await db_session.commit()

        # place_from_event_text already turns an outage into None; here the
        # geocoder itself fails underneath it.
        from app.workers.tasks import _refresh_entry_location

        entry_id = entry.id
        down = AsyncMock(side_effect=GeocodingUnavailable("timeout"))
        with (
            _worker_uses(db_session),
            patch("app.services.geocoding.search_places", down),
            patch("app.workers.open_meteo.fetch_daily", AsyncMock(return_value=WEATHER)),
        ):
            await _refresh_entry_location(str(entry_id))
        stored = await _reload(db_session, entry_id)

        assert stored.location_source == "home"

    async def test_parent_place_survives_regeneration(self, client, db_session):
        owner, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry.location_name, entry.lat, entry.lon = "Mukilteo, Washington", 47.94454, -122.30458
        entry.location_source = "parent"
        photo = await make_photo(db_session, user=owner, lat=PHOTO[0], lon=PHOTO[1])
        await make_entry_photo(db_session, entry=entry, photo=photo)
        await db_session.commit()

        from app.workers.tasks import _generate_entry_draft

        entry_id = entry.id
        with (
            _worker_uses(db_session),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock(return_value=None)),
            patch("app.workers.open_meteo.fetch_daily", AsyncMock(return_value=WEATHER)),
        ):
            await _generate_entry_draft(str(entry_id), "regenerate")
        stored = await _reload(db_session, entry_id)

        assert stored.location_source == "parent"
        assert _coords(stored) == (47.94454, -122.30458)


class TestWeatherFollowsThePlace:
    async def test_place_change_replaces_weather(self, client, db_session):
        owner, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry.location_name, entry.lat, entry.lon = "Everett, Washington", HOME[0], HOME[1]
        entry.location_source = "home"
        db_session.add(
            Enrichment(
                entry_id=entry.id, kind="weather", payload={"old": True}, source="open_meteo",
                captured_for_at=datetime(2026, 5, 16, tzinfo=UTC),
                fetched_at=datetime.now(tz=UTC),
            )
        )  # fmt: skip
        photo = await make_photo(db_session, user=owner, lat=PHOTO[0], lon=PHOTO[1])
        await make_entry_photo(db_session, entry=entry, photo=photo)
        await db_session.commit()
        weather = AsyncMock(return_value=WEATHER)
        entry_id = entry.id

        await _refresh(db_session, entry, weather=weather)

        rows = (
            await db_session.execute(select(Enrichment).where(Enrichment.entry_id == entry_id))
        ).scalars().all()
        assert [r.payload for r in rows] == [WEATHER]
        lat, lon, day = weather.await_args.args
        assert (round(lat, 5), round(lon, 5), day) == (
            round(PHOTO[0], 5), round(PHOTO[1], 5), date(2026, 5, 16),
        )  # fmt: skip

    async def test_unchanged_place_keeps_its_weather(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry.location_name, entry.lat, entry.lon = "Everett, Washington", HOME[0], HOME[1]
        entry.location_source = "home"
        db_session.add(
            Enrichment(
                entry_id=entry.id, kind="weather", payload={"old": True}, source="open_meteo",
                captured_for_at=datetime(2026, 5, 16, tzinfo=UTC),
                fetched_at=datetime.now(tz=UTC),
            )
        )  # fmt: skip
        await db_session.commit()
        weather = AsyncMock(return_value=WEATHER)
        entry_id = entry.id

        await _refresh(db_session, entry, weather=weather)

        rows = (
            await db_session.execute(select(Enrichment).where(Enrichment.entry_id == entry_id))
        ).scalars().all()
        assert [r.payload for r in rows] == [{"old": True}]
        weather.assert_not_awaited()


class TestPhotoChangesQueueARefresh:
    async def _owner_entry_photo(self, client, db_session, email):
        r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-16"},
                headers=auth,
            )
        ).json()  # fmt: skip
        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        photo = await make_photo(db_session, user=user, lat=PHOTO[0], lon=PHOTO[1])
        await db_session.commit()
        return auth, entry, photo

    async def test_attaching_a_photo_queues_a_refresh(self, client, db_session, sync_engine):
        auth, entry, photo = await self._owner_entry_photo(
            client, db_session, "loc-attach@example.com"
        )
        seen = {}

        def _on_queue(entry_id, *args, **kwargs):
            with sync_engine.connect() as conn:
                seen["linked"] = conn.execute(
                    text("SELECT 1 FROM entry_photos WHERE entry_id = :e AND photo_id = :p"),
                    {"e": entry["id"], "p": str(photo.id)},
                ).first() is not None
            seen["entry_id"] = entry_id

        with patch("app.workers.tasks.refresh_entry_location.delay", side_effect=_on_queue):
            r = await client.post(
                f"/v1/entries/{entry['id']}/photos", json={"photo_id": str(photo.id)},
                headers=auth,
            )  # fmt: skip

        assert r.status_code == 201
        assert seen == {"linked": True, "entry_id": entry["id"]}

    async def test_detaching_a_photo_queues_a_refresh(self, client, db_session, sync_engine):
        auth, entry, photo = await self._owner_entry_photo(
            client, db_session, "loc-detach@example.com"
        )
        await client.post(
            f"/v1/entries/{entry['id']}/photos", json={"photo_id": str(photo.id)}, headers=auth
        )
        seen = {}

        def _on_queue(entry_id, *args, **kwargs):
            with sync_engine.connect() as conn:
                seen["linked"] = conn.execute(
                    text("SELECT 1 FROM entry_photos WHERE entry_id = :e AND photo_id = :p"),
                    {"e": entry["id"], "p": str(photo.id)},
                ).first() is not None
            seen["entry_id"] = entry_id

        with patch("app.workers.tasks.refresh_entry_location.delay", side_effect=_on_queue):
            r = await client.delete(f"/v1/entries/{entry['id']}/photos/{photo.id}", headers=auth)

        assert r.status_code == 204
        assert seen == {"linked": False, "entry_id": entry["id"]}
