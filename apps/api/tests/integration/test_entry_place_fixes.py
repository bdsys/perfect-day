"""Place and time-zone fixes (TR-19, TR-21, TR-22)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AutoCreationRule, Entry
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_diary, make_event, make_user
from tests.integration.test_entry_location import _api_entry, _attach_event, _patch


class TestGeocodeTimeout:
    async def test_geocode_endpoint_uses_short_timeout(self, client: AsyncClient):
        r = await client.post(
            "/v1/auth/register", json={"email": "geo-timeout@example.com", "password": "Password1!"}
        )
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

        response = MagicMock(status_code=200)
        response.json.return_value = {"results": []}
        http = MagicMock()
        http.__aenter__.return_value = http
        http.__aexit__.return_value = False

        async def _get(*args, **kwargs):
            return response

        http.get = _get
        with patch("app.services.geocoding.httpx.AsyncClient", return_value=http) as cls:
            r = await client.get("/v1/geocode", params={"q": "Muk"}, headers=auth)

        assert r.status_code == 200
        assert cls.call_args.kwargs["timeout"] == 5


class TestTypedDateKept:
    async def test_patch_date_and_tz_keeps_the_typed_date(self, client, db_session):
        auth, _, entry = await _api_entry(client, "tr21-both@example.com")
        # 9 am on the 3rd in Tokyo is the 2nd in Los Angeles; the events say the 3rd.
        await _attach_event(
            db_session, entry["id"], "2026-06-03T09:00:00+09:00", "2026-06-03T10:00:00+09:00"
        )

        r = await _patch(
            client,
            entry["id"],
            auth,
            {"entry_date": "2026-06-02", "tz_override": "Asia/Tokyo"},
        )

        assert r.status_code == 200, r.text
        assert r.json()["tz_override"] == "Asia/Tokyo"
        assert r.json()["entry_date"] == "2026-06-02"

    async def test_patch_tz_alone_still_moves_the_date(self, client, db_session):
        auth, _, entry = await _api_entry(client, "tr21-tz@example.com")
        await _attach_event(
            db_session, entry["id"], "2026-06-03T09:00:00+09:00", "2026-06-03T10:00:00+09:00"
        )

        r = await _patch(client, entry["id"], auth, {"tz_override": "Asia/Tokyo"})

        assert r.json()["entry_date"] == "2026-06-03"


@pytest.fixture
def wire_worker_db(db_url):
    import app.core.database as db_module

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    original_engine = db_module._engine
    original_factory = db_module._session_factory
    db_module._engine = engine
    db_module._session_factory = factory
    yield
    db_module._engine = original_engine
    db_module._session_factory = original_factory


class TestSeriesJoinRefreshesPlace:
    async def test_event_joining_claimed_entry_refreshes_place(
        self, db_session: AsyncSession, wire_worker_db
    ):
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        db_session.add(
            AutoCreationRule(
                diary_id=diary.id,
                name="Standup",
                enabled=True,
                condition={
                    "op": "AND",
                    "children": [
                        {"field": "title", "op": "contains", "value": "Weekly standup"}
                    ],
                },
                options={"recurring": "per_series", "multi_day": "per_day"},
            )
        )
        await db_session.commit()
        base = {
            "summary": "Weekly standup",
            "recurringEventId": "rec_1",
            "start": {"dateTime": "2026-06-01T09:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "Seattle, WA",
            "status": "",
        }
        first = await make_event(db_session, diary_id=diary.id, payload=base)
        second = await make_event(
            db_session,
            diary_id=diary.id,
            payload={**base, "start": {"dateTime": "2026-06-08T09:00:00Z"}},
        )

        with (
            patch("app.workers.tasks.generate_entry_draft") as draft,
            patch("app.workers.tasks.queue_location_refresh") as refresh,
        ):
            draft.delay = MagicMock()
            await evaluate_event_against_rules(str(first.id), str(diary.id), db_session)
            refresh.assert_not_called()  # a new entry gets its place with its draft
            await evaluate_event_against_rules(str(second.id), str(diary.id), db_session)

        entry_id = (await db_session.execute(select(Entry.id))).scalar_one()
        refresh.assert_called_once_with(entry_id)
