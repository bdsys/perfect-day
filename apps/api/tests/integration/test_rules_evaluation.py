"""Integration tests for evaluate_event_against_rules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AutoCreationRule, Diary, Entry, EntryRuleMatch, RuleSeriesClaim
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_diary, make_event, make_user

# ---------------------------------------------------------------------------
# Wire the worker's db_session at the test database engine
# (same pattern as test_calendar_event_unattached.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def wire_worker_db(db_url):
    """Point the worker's db_session at the test database engine."""
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SOCCER_CONDITION = {
    "op": "AND",
    "children": [
        {
            "field": "title",
            "op": "contains",
            "value": "Soccer",
            "case_sensitive": False,
        }
    ],
}

_STANDUP_CONDITION = {
    "op": "AND",
    "children": [
        {
            "field": "title",
            "op": "contains",
            "value": "Weekly standup",
            "case_sensitive": False,
        }
    ],
}

_SOCCER_PAYLOAD = {
    "summary": "Soccer practice",
    "start": {"dateTime": "2026-06-01T10:00:00Z"},
    "end": {},
    "attendees": [],
    "description": "",
    "location": "",
    "status": "",
}


async def _make_rule(
    db: AsyncSession,
    diary: Diary,
    *,
    condition: dict,
    options: dict,
    enabled: bool = True,
) -> AutoCreationRule:
    rule = AutoCreationRule(
        diary_id=diary.id,
        name="Test rule",
        enabled=enabled,
        condition=condition,
        options=options,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvaluateEventAgainstRules:
    async def test_matching_rule_creates_entry(self, db_session: AsyncSession):
        """A matching enabled rule creates an Entry, attaches the event, records match."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        rule = await _make_rule(
            db_session,
            diary,
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
        )

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_SOCCER_PAYLOAD,
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        # Refresh from DB
        await db_session.refresh(event)
        assert event.entry_id is not None, "event must be attached to an entry"

        entry_result = await db_session.execute(
            select(Entry).where(Entry.id == event.entry_id)
        )
        entry = entry_result.scalar_one()
        assert entry.created_by == "auto"
        assert entry.creation_source == "rule"
        assert entry.status == "draft"

        match_result = await db_session.execute(
            select(EntryRuleMatch).where(
                EntryRuleMatch.entry_id == entry.id,
                EntryRuleMatch.rule_id == rule.id,
            )
        )
        assert match_result.scalar_one_or_none() is not None, "EntryRuleMatch row must exist"

        mock_task.delay.assert_called_once_with(str(entry.id))

    async def test_non_matching_rule_no_entry(self, db_session: AsyncSession):
        """A rule that does not match the event payload must not create any Entry."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
        )

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                **_SOCCER_PAYLOAD,
                "summary": "Piano lesson",  # does NOT contain "Soccer"
            },
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        await db_session.refresh(event)
        assert event.entry_id is None, "event must remain unattached"

        entry_count_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        assert entry_count_result.scalars().all() == [], "no Entry must be created"

        match_count_result = await db_session.execute(select(EntryRuleMatch))
        assert match_count_result.scalars().all() == [], "no EntryRuleMatch must be created"

        mock_task.delay.assert_not_called()

    async def test_per_series_second_instance_reuses_entry(self, db_session: AsyncSession):
        """Two instances of a recurring event share one entry via RuleSeriesClaim."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        rule = await _make_rule(
            db_session,
            diary,
            condition=_STANDUP_CONDITION,
            options={"recurring": "per_series", "multi_day": "per_day"},
        )

        recurring_payload_base = {
            "summary": "Weekly standup",
            "recurringEventId": "recurring_abc",
            "start": {"dateTime": "2026-06-01T09:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        }

        event1 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=recurring_payload_base,
        )
        event2 = await make_event(
            db_session,
            diary_id=diary.id,
            payload={**recurring_payload_base, "start": {"dateTime": "2026-06-08T09:00:00Z"}},
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event1.id), str(diary.id), db_session)
            await evaluate_event_against_rules(str(event2.id), str(diary.id), db_session)

        await db_session.refresh(event1)
        await db_session.refresh(event2)

        assert event1.entry_id is not None
        assert event1.entry_id == event2.entry_id, "both events must share the same entry"

        # Only one Entry row
        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = entries_result.scalars().all()
        assert len(entries) == 1, "exactly one Entry must be created for the series"

        # One RuleSeriesClaim
        claims_result = await db_session.execute(
            select(RuleSeriesClaim).where(RuleSeriesClaim.rule_id == rule.id)
        )
        claims = claims_result.scalars().all()
        assert len(claims) == 1, "exactly one RuleSeriesClaim must exist"

        # One EntryRuleMatch (PK is (entry_id, rule_id) — unique per entry+rule)
        matches_result = await db_session.execute(
            select(EntryRuleMatch).where(EntryRuleMatch.rule_id == rule.id)
        )
        matches = matches_result.scalars().all()
        assert len(matches) == 1, "one EntryRuleMatch for (entry, rule) pair"

        # LLM queued exactly once — only for the new entry
        mock_task.delay.assert_called_once_with(str(entries[0].id))

    async def test_disabled_rule_no_entry(self, db_session: AsyncSession):
        """A disabled rule must be ignored even when the event matches its condition."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
            enabled=False,
        )

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_SOCCER_PAYLOAD,
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        await db_session.refresh(event)
        assert event.entry_id is None, "disabled rule must not create an entry"

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        assert entries_result.scalars().all() == []

        mock_task.delay.assert_not_called()

    async def test_tier_limit_skips_rule(self, db_session: AsyncSession):
        """When the tier check fails the rule is skipped and no entry is created."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
        )

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_SOCCER_PAYLOAD,
        )

        with (
            patch(
                "app.workers.rules.try_enforce_entry_tier_limit",
                return_value=(False, "entry limit reached"),
            ),
            patch("app.workers.tasks.generate_entry_draft") as mock_task,
        ):
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        await db_session.refresh(event)
        assert event.entry_id is None, "tier-limited rule must not create an entry"

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        assert entries_result.scalars().all() == []

        mock_task.delay.assert_not_called()
