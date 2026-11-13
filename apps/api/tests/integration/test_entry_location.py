"""Per-entry place and time zone (PoC item 24).

Spec: docs/superpowers/specs/2026-11-12-per-entry-location-design.md
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy import text as text_sql
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

from app.models import Diary, Enrichment, Entry, User  # noqa: E402
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


# ---------------------------------------------------------------------------
# Task 4: the parent sets an entry's place and time zone
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402

from app.models import DiaryPermission  # noqa: E402

PLACE_IN = {"name": "Mukilteo, Washington", "lat": 47.94454, "lon": -122.30458}


async def _api_entry(client, email: str, *, timezone: str = "America/Los_Angeles"):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": timezone}, headers=auth)
    ).json()
    entry = (
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-16"}, headers=auth
        )
    ).json()
    return auth, diary, entry


async def _attach_event(db_session, entry_id: str, start: str, end: str):
    await make_event(
        db_session,
        diary_id=(
            await db_session.execute(select(Entry.diary_id).where(Entry.id == _uuid.UUID(entry_id)))
        ).scalar_one(),
        payload={"summary": "Flight", "start": {"dateTime": start}, "end": {"dateTime": end}},
    )
    # make_event cannot link by id alone; link the newest event to the entry.
    from app.models import Event

    newest = (
        await db_session.execute(select(Event).order_by(Event.created_at.desc()).limit(1))
    ).scalar_one()
    newest.entry_id = _uuid.UUID(entry_id)
    await db_session.commit()


async def _patch(client, entry_id, auth, body, queued: list | None = None):
    def _on_queue(entry_id, *args, **kwargs):
        if queued is not None:
            queued.append((entry_id, kwargs))

    with patch("app.workers.tasks.refresh_entry_location.delay", side_effect=_on_queue):
        return await client.patch(f"/v1/entries/{entry_id}", json=body, headers=auth)


class TestParentSetsThePlace:
    async def test_parent_sets_a_place(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-set@example.com")
        queued: list = []

        r = await _patch(client, entry["id"], auth, {"location": PLACE_IN}, queued)

        assert r.status_code == 200, r.text
        assert r.json()["location"] == {**PLACE_IN, "source": "parent"}
        assert queued == [(entry["id"], {"refetch_weather": True})]

    async def test_null_location_returns_to_automatic(self, client, db_session):
        auth, diary, entry = await _api_entry(client, "t4-auto@example.com")
        await _patch(client, entry["id"], auth, {"location": PLACE_IN})
        home = (await db_session.execute(select(Diary).where(Diary.id == _uuid.UUID(diary["id"]))))
        home = home.scalar_one()
        home.lat, home.lon, home.location_name = HOME[0], HOME[1], "Everett, Washington"
        await db_session.commit()
        queued: list = []

        r = await _patch(client, entry["id"], auth, {"location": None}, queued)
        assert r.status_code == 200
        assert r.json()["location"] is None
        assert queued == [(entry["id"], {"refetch_weather": True})]

        stored = await _refresh(db_session, (await _reload(db_session, _uuid.UUID(entry["id"]))))
        assert stored.location_source == "home"

    async def test_viewer_cannot_change_the_place(self, client, db_session):
        owner_auth, diary, entry = await _api_entry(client, "t4-owner@example.com")
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner_auth)
        r = await client.post(
            "/v1/auth/register", json={"email": "t4-viewer@example.com", "password": "Password1!"}
        )
        viewer_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        viewer = (
            await db_session.execute(select(User).where(User.email == "t4-viewer@example.com"))
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=_uuid.UUID(diary["id"]), user_id=viewer.id, role="viewer")
        )
        await db_session.commit()

        r = await _patch(client, entry["id"], viewer_auth, {"location": PLACE_IN})

        assert r.status_code == 403

    async def test_entry_out_shows_place_to_a_viewer_of_a_published_entry(
        self, client, db_session
    ):
        owner_auth, diary, entry = await _api_entry(client, "t4-show@example.com")
        await _patch(client, entry["id"], owner_auth, {"location": PLACE_IN})
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner_auth)
        r = await client.post(
            "/v1/auth/register", json={"email": "t4-see@example.com", "password": "Password1!"}
        )
        viewer_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        viewer = (
            await db_session.execute(select(User).where(User.email == "t4-see@example.com"))
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=_uuid.UUID(diary["id"]), user_id=viewer.id, role="viewer")
        )
        await db_session.commit()

        r = await client.get(f"/v1/entries/{entry['id']}", headers=viewer_auth)

        # The place's name, but never its coordinates: only the owner gets those.
        assert r.status_code == 200
        assert r.json()["location"] == {
            "name": PLACE_IN["name"], "lat": None, "lon": None, "source": "parent",
        }  # fmt: skip


class TestCoordinatesStayWithTheOwner:
    """A photo's GPS can pinpoint a home or a school (side-agent note, 2026-11-12).

    Only the diary owner gets exact coordinates, for the entry's place and for
    its photos. Co-parents and viewers get the place name.
    """

    async def _shared_entry_with_photo(self, client, db_session, role: str, prefix: str):
        owner_auth, diary, entry = await _api_entry(client, f"{prefix}-owner@example.com")
        stored = await _reload(db_session, _uuid.UUID(entry["id"]))  # expires the session
        owner = (
            await db_session.execute(
                select(User).where(User.email == f"{prefix}-owner@example.com")
            )
        ).scalar_one()
        photo = await make_photo(db_session, user=owner, lat=PHOTO[0], lon=PHOTO[1])
        await make_entry_photo(db_session, entry=stored, photo=photo)
        stored.lat, stored.lon, stored.location_source = PHOTO[0], PHOTO[1], "photo"
        await db_session.commit()
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner_auth)
        r = await client.post(
            "/v1/auth/register",
            json={"email": f"{prefix}-{role}@example.com", "password": "Password1!"},
        )
        other_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        other = (
            await db_session.execute(
                select(User).where(User.email == f"{prefix}-{role}@example.com")
            )
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=_uuid.UUID(diary["id"]), user_id=other.id, role=role)
        )
        await db_session.commit()
        return owner_auth, other_auth, diary, entry

    async def test_owner_gets_exact_coordinates(self, client, db_session):
        owner_auth, _, _, entry = await self._shared_entry_with_photo(
            client, db_session, "viewer", "gps-own"
        )

        body = (await client.get(f"/v1/entries/{entry['id']}", headers=owner_auth)).json()

        assert (round(body["location"]["lat"], 5), round(body["location"]["lon"], 5)) == (
            round(PHOTO[0], 5), round(PHOTO[1], 5),
        )  # fmt: skip
        assert body["photos"][0]["lat"] is not None

    async def test_viewer_gets_no_coordinates(self, client, db_session):
        _, viewer_auth, diary, entry = await self._shared_entry_with_photo(
            client, db_session, "viewer", "gps-view"
        )

        one = (await client.get(f"/v1/entries/{entry['id']}", headers=viewer_auth)).json()
        listed = (
            await client.get(f"/v1/diaries/{diary['id']}/entries", headers=viewer_auth)
        ).json()

        for body in (one, listed[0]):
            assert body["location"] == {"name": None, "lat": None, "lon": None, "source": "photo"}
            assert [(p["lat"], p["lon"]) for p in body["photos"]] == [(None, None)]

    async def test_editor_gets_no_coordinates_even_when_editing(self, client, db_session):
        _, editor_auth, _, entry = await self._shared_entry_with_photo(
            client, db_session, "editor", "gps-edit"
        )

        read = (await client.get(f"/v1/entries/{entry['id']}", headers=editor_auth)).json()
        r = await _patch(client, entry["id"], editor_auth, {"title": "Edited"})

        for body in (read, r.json()):
            assert body["location"]["lat"] is None
            assert body["photos"][0]["lat"] is None


class TestTimeZoneOverride:
    async def test_time_zone_moves_the_date_of_an_entry_with_events(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-tz@example.com")
        # 9 am in Tokyo on the 17th is the 16th in Los Angeles (the diary's zone).
        await _attach_event(
            db_session, entry["id"], "2026-05-17T09:00:00+09:00", "2026-05-17T10:00:00+09:00"
        )
        queued: list = []

        r = await _patch(client, entry["id"], auth, {"tz_override": "Asia/Tokyo"}, queued)

        assert r.status_code == 200, r.text
        assert r.json()["tz_override"] == "Asia/Tokyo"
        assert (r.json()["entry_date"], r.json()["entry_end_date"]) == ("2026-05-17", None)
        assert queued == [(entry["id"], {"refetch_weather": True})]

    async def test_time_zone_leaves_an_entry_without_events_alone(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-noevents@example.com")
        queued: list = []

        r = await _patch(client, entry["id"], auth, {"tz_override": "Asia/Tokyo"}, queued)

        assert r.status_code == 200
        assert r.json()["entry_date"] == "2026-05-16"
        assert r.json()["tz_override"] == "Asia/Tokyo"
        assert queued == []

    async def test_multi_day_entry_dates_recomputed(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-multi@example.com")
        await _attach_event(
            db_session, entry["id"], "2026-05-17T08:00:00+09:00", "2026-05-18T20:00:00+09:00"
        )

        r = await _patch(client, entry["id"], auth, {"tz_override": "Asia/Tokyo"})

        assert (r.json()["entry_date"], r.json()["entry_end_date"]) == ("2026-05-17", "2026-05-18")

    async def test_clearing_the_time_zone_uses_the_diary_zone(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-clear@example.com")
        await _attach_event(
            db_session, entry["id"], "2026-05-17T09:00:00+09:00", "2026-05-17T10:00:00+09:00"
        )
        await _patch(client, entry["id"], auth, {"tz_override": "Asia/Tokyo"})

        r = await _patch(client, entry["id"], auth, {"tz_override": None})

        assert r.json()["tz_override"] is None
        assert r.json()["entry_date"] == "2026-05-16"

    async def test_unknown_time_zone_is_422(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-bad@example.com")

        r = await _patch(client, entry["id"], auth, {"tz_override": "Mars/Olympus_Mons"})

        assert r.status_code == 422

    async def test_editing_the_date_by_hand_queues_a_refresh(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-date@example.com")
        queued: list = []

        r = await _patch(client, entry["id"], auth, {"entry_date": "2026-05-20"}, queued)

        assert r.status_code == 200
        assert queued == [(entry["id"], {"refetch_weather": True})]

    async def test_editing_only_the_text_queues_nothing(self, client, db_session):
        auth, _, entry = await _api_entry(client, "t4-text@example.com")
        queued: list = []

        await _patch(client, entry["id"], auth, {"title": "A new title"}, queued)

        assert queued == []


# ---------------------------------------------------------------------------
# Task 5: the diary's home place
# ---------------------------------------------------------------------------

HOME_IN = {"location_name": "Everett, Washington", "lat": HOME[0], "lon": HOME[1]}


class TestDiaryHomePlace:
    async def test_owner_sets_home_place_and_entries_follow(self, client, db_session):
        auth, diary, entry = await _api_entry(client, "t5-home@example.com")
        parent_entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2026-05-17"},
                headers=auth,
            )
        ).json()  # fmt: skip
        await _patch(client, parent_entry["id"], auth, {"location": PLACE_IN})
        queued_diaries: list = []

        with patch(
            "app.workers.tasks.refresh_diary_locations.delay",
            side_effect=lambda diary_id: queued_diaries.append(diary_id),
        ):
            r = await client.patch(f"/v1/diaries/{diary['id']}", json=HOME_IN, headers=auth)

        assert r.status_code == 200, r.text
        assert r.json()["location_name"] == "Everett, Washington"
        assert queued_diaries == [diary["id"]]

        # The diary task queues a refresh for entries without a place of their own.
        from app.workers.tasks import _refresh_diary_locations

        queued_entries: list = []
        with (
            _worker_uses(db_session),
            patch(
                "app.workers.tasks.refresh_entry_location.delay",
                side_effect=lambda entry_id, **kw: queued_entries.append(entry_id),
            ),
        ):
            await _refresh_diary_locations(diary["id"])
        assert queued_entries == [entry["id"]]

        stored = await _refresh(db_session, await _reload(db_session, _uuid.UUID(entry["id"])))
        assert (stored.location_source, stored.location_name) == ("home", "Everett, Washington")

    async def test_editor_cannot_set_home_place(self, client, db_session):
        _, diary, _ = await _api_entry(client, "t5-owner@example.com")
        r = await client.post(
            "/v1/auth/register", json={"email": "t5-editor@example.com", "password": "Password1!"}
        )
        editor_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        editor = (
            await db_session.execute(select(User).where(User.email == "t5-editor@example.com"))
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=_uuid.UUID(diary["id"]), user_id=editor.id, role="editor")
        )
        await db_session.commit()

        r = await client.patch(f"/v1/diaries/{diary['id']}", json=HOME_IN, headers=editor_auth)

        assert r.status_code == 403

    async def test_partial_home_place_is_422(self, client, db_session):
        auth, diary, _ = await _api_entry(client, "t5-partial@example.com")

        r = await client.patch(
            f"/v1/diaries/{diary['id']}",
            json={"location_name": "Everett, Washington", "lat": HOME[0]},
            headers=auth,
        )

        assert r.status_code == 422

    async def test_clearing_the_home_place_clears_its_name(self, client, db_session):
        auth, diary, _ = await _api_entry(client, "t5-clear@example.com")
        with patch("app.workers.tasks.refresh_diary_locations.delay"):
            await client.patch(f"/v1/diaries/{diary['id']}", json=HOME_IN, headers=auth)
            r = await client.patch(
                f"/v1/diaries/{diary['id']}", json={"lat": None, "lon": None}, headers=auth
            )

        assert r.status_code == 200
        assert (r.json()["lat"], r.json()["lon"], r.json()["location_name"]) == (None, None, None)

    async def test_parent_place_ignores_home_change(self, client, db_session):
        auth, diary, entry = await _api_entry(client, "t5-keep@example.com")
        await _patch(client, entry["id"], auth, {"location": PLACE_IN})
        with patch("app.workers.tasks.refresh_diary_locations.delay"):
            await client.patch(f"/v1/diaries/{diary['id']}", json=HOME_IN, headers=auth)

        stored = await _refresh(db_session, await _reload(db_session, _uuid.UUID(entry["id"])))

        assert (stored.location_source, stored.location_name) == ("parent", PLACE_IN["name"])

    async def test_renaming_the_diary_queues_nothing(self, client, db_session):
        auth, diary, _ = await _api_entry(client, "t5-rename@example.com")
        queued: list = []
        with patch(
            "app.workers.tasks.refresh_diary_locations.delay",
            side_effect=lambda diary_id: queued.append(diary_id),
        ):
            await client.patch(f"/v1/diaries/{diary['id']}", json={"name": "New"}, headers=auth)

        assert queued == []


class TestEntryOutSaysWhoCanEdit:
    """The entry page hides Edit location from viewers (Task 7 ruling)."""

    async def test_owner_can_edit_and_viewer_cannot(self, client, db_session):
        owner_auth, diary, entry = await _api_entry(client, "t7-can@example.com")
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner_auth)
        r = await client.post(
            "/v1/auth/register", json={"email": "t7-cannot@example.com", "password": "Password1!"}
        )
        viewer_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        viewer = (
            await db_session.execute(select(User).where(User.email == "t7-cannot@example.com"))
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=_uuid.UUID(diary["id"]), user_id=viewer.id, role="viewer")
        )
        await db_session.commit()

        own = (await client.get(f"/v1/entries/{entry['id']}", headers=owner_auth)).json()
        seen = (await client.get(f"/v1/entries/{entry['id']}", headers=viewer_auth)).json()

        assert (own["can_edit"], seen["can_edit"]) == (True, False)


# ---------------------------------------------------------------------------
# Final review I2/I3: overlapping jobs and row locks
# ---------------------------------------------------------------------------


class TestJobsThatOverlap:
    async def test_an_unchanged_place_is_not_rewritten(self, client, db_session):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry_id = entry.id
        await _refresh(db_session, entry)
        first = (await _reload(db_session, entry_id)).updated_at

        await _refresh(db_session, await _reload(db_session, entry_id))

        assert (await _reload(db_session, entry_id)).updated_at == first

    async def test_a_place_the_parent_sets_mid_run_is_kept(
        self, client, db_session, sync_engine
    ):
        _, _, entry = await _entry_in_diary(db_session)
        await make_event(db_session, entry=entry, payload={"summary": "x",
                         "location": "Mukilteo Library, Mukilteo, WA"})  # fmt: skip
        await db_session.commit()
        entry_id = entry.id

        async def _parent_saves_while_we_geocode(text):
            # Another request commits the parent's own place while this job is geocoding.
            with sync_engine.begin() as conn:
                conn.execute(text_sql("SET LOCAL lock_timeout = '2s'"))
                conn.execute(
                    text_sql(
                        "UPDATE entries SET location_name = 'Everett, Washington', lat = :lat,"
                        " lon = :lon, location_source = 'parent' WHERE id = :id"
                    ),
                    {"lat": HOME[0], "lon": HOME[1], "id": str(entry_id)},
                )
            return MUKILTEO

        stored = await _refresh(db_session, entry, geocode=_parent_saves_while_we_geocode)

        assert (stored.location_source, stored.location_name) == ("parent", "Everett, Washington")
        assert _coords(stored) == (round(HOME[0], 5), round(HOME[1], 5))

    async def test_the_entry_is_not_locked_while_weather_is_fetched(
        self, client, db_session, sync_engine
    ):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry_id = entry.id
        seen = {}

        async def _a_save_arrives_during_the_fetch(lat, lon, day):
            try:
                with sync_engine.begin() as conn:
                    conn.execute(text_sql("SET LOCAL lock_timeout = '2s'"))
                    conn.execute(
                        text_sql("UPDATE entries SET title = 'Saved meanwhile' WHERE id = :id"),
                        {"id": str(entry_id)},
                    )
                seen["saved"] = True
            except Exception as exc:  # lock timeout: the job held the row
                seen["saved"] = type(exc).__name__
            return WEATHER

        await _refresh(db_session, entry, weather=_a_save_arrives_during_the_fetch)

        assert seen == {"saved": True}

    async def test_weather_for_a_place_that_changed_meanwhile_is_dropped(
        self, client, db_session, sync_engine
    ):
        _, _, entry = await _entry_in_diary(db_session, home=HOME)
        entry_id = entry.id

        seen = {}

        async def _place_moves_during_the_fetch(lat, lon, day):
            # Another job commits a new place (and would fetch its own weather).
            try:
                with sync_engine.begin() as conn:
                    conn.execute(text_sql("SET LOCAL lock_timeout = '2s'"))
                    conn.execute(
                        text_sql(
                            "UPDATE entries SET lat = :lat, lon = :lon, location_source = 'photo',"
                            " location_name = NULL WHERE id = :id"
                        ),
                        {"lat": PHOTO[0], "lon": PHOTO[1], "id": str(entry_id)},
                    )
                seen["moved"] = True
            except Exception as exc:  # lock timeout: the job held the row
                seen["moved"] = type(exc).__name__
            return WEATHER

        await _refresh(db_session, entry, weather=_place_moves_during_the_fetch)

        rows = (
            await db_session.execute(select(Enrichment).where(Enrichment.entry_id == entry_id))
        ).scalars().all()
        assert seen == {"moved": True}
        assert rows == []
