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

    async def test_already_attached_event_skipped(self, db_session: AsyncSession):
        """An event that already has entry_id set should be skipped entirely."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")

        # Create an existing entry to pre-attach the event to
        from tests.fixtures.factories import make_entry

        existing_entry = await make_entry(db_session, diary=diary)

        rule = AutoCreationRule(
            diary_id=diary.id,
            name="test rule",
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
            enabled=True,
        )
        db_session.add(rule)
        await db_session.flush()

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_SOCCER_PAYLOAD,
        )
        # Pre-attach the event to the existing entry
        event.entry_id = existing_entry.id
        await db_session.commit()

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        # No new entries should have been created beyond the pre-existing one
        result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = result.scalars().all()
        assert len(entries) == 1
        assert entries[0].id == existing_entry.id

        mock_task.delay.assert_not_called()

    async def test_two_rules_matching_same_event(self, db_session: AsyncSession):
        """Two matching rules create two separate entries; event attaches to the first."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")

        rule1 = AutoCreationRule(
            diary_id=diary.id,
            name="rule 1",
            condition=_SOCCER_CONDITION,
            options={"recurring": "per_instance", "multi_day": "per_day"},
            enabled=True,
        )
        rule2 = AutoCreationRule(
            diary_id=diary.id,
            name="rule 2",
            condition={
                "op": "AND",
                "children": [
                    {
                        "field": "title",
                        "op": "contains",
                        "value": "practice",
                        "case_sensitive": False,
                    }
                ],
            },
            options={"recurring": "per_instance", "multi_day": "per_day"},
            enabled=True,
        )
        db_session.add(rule1)
        db_session.add(rule2)
        await db_session.flush()

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_SOCCER_PAYLOAD,  # summary "Soccer practice" matches both rules
        )
        await db_session.commit()

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        await db_session.refresh(event)
        # Event is attached to some entry
        assert event.entry_id is not None

        # Two entries were created (one per matching rule)
        result = await db_session.execute(
            select(Entry)
            .where(Entry.diary_id == diary.id)
            .where(Entry.creation_source == "rule")
        )
        entries = result.scalars().all()
        assert len(entries) == 2

        # Event is attached to exactly one of those entries
        entry_ids = {e.id for e in entries}
        assert event.entry_id in entry_ids

        # One EntryRuleMatch per rule (two total)
        match_result = await db_session.execute(
            select(EntryRuleMatch).where(
                EntryRuleMatch.entry_id.in_(entry_ids)
            )
        )
        matches = match_result.scalars().all()
        assert len(matches) == 2

        # LLM generation queued for both entries
        assert mock_task.delay.call_count == 2


# ---------------------------------------------------------------------------
# Multi-day entry grouping (item 15)
# ---------------------------------------------------------------------------


_TRIP_CONDITION = {
    "op": "AND",
    "children": [
        {
            "field": "title",
            "op": "contains",
            "value": "Trip",
            "case_sensitive": False,
        }
    ],
}


def _spanning_payload(summary: str, start_date: str, end_date_exclusive: str) -> dict:
    """Build an all-day spanning payload. Google's all-day end is exclusive."""
    return {
        "summary": summary,
        "start": {"date": start_date},
        "end": {"date": end_date_exclusive},
        "attendees": [],
        "description": "",
        "location": "",
        "status": "",
    }


class TestMultiDayGrouping:
    async def test_same_range_spanning_events_group(self, db_session: AsyncSession):
        """Two spanning events with the same (start, end) range share one entry."""
        from datetime import date

        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        rule = await _make_rule(
            db_session,
            diary,
            condition=_TRIP_CONDITION,
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )

        # Both span 2026-06-01 → 2026-06-03 (Google end is exclusive, so 06-04).
        event1 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip flight out", "2026-06-01", "2026-06-04"),
        )
        event2 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip dinner", "2026-06-01", "2026-06-04"),
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event1.id), str(diary.id), db_session)
            await evaluate_event_against_rules(str(event2.id), str(diary.id), db_session)

        await db_session.refresh(event1)
        await db_session.refresh(event2)

        assert event1.entry_id is not None
        assert event1.entry_id == event2.entry_id, "both events must share one entry"

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = entries_result.scalars().all()
        assert len(entries) == 1, "exactly one Entry must exist"

        entry = entries[0]
        assert entry.entry_date == date(2026, 6, 1)
        assert entry.entry_end_date == date(2026, 6, 3)
        assert entry.creation_source == "rule"

        # One EntryRuleMatch, not two.
        matches_result = await db_session.execute(
            select(EntryRuleMatch).where(EntryRuleMatch.rule_id == rule.id)
        )
        matches = matches_result.scalars().all()
        assert len(matches) == 1

        # Draft generation queued twice — once for the new entry, once for
        # the regeneration when event2 was grouped in.
        assert mock_task.delay.call_count == 2
        called_with = {c.args[0] for c in mock_task.delay.call_args_list}
        assert called_with == {str(entry.id)}

    async def test_different_range_spanning_events_do_not_group(
        self, db_session: AsyncSession
    ):
        """Spanning events with different ranges must create separate entries."""
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_TRIP_CONDITION,
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )

        event1 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip A", "2026-06-01", "2026-06-04"),
        )
        event2 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip B", "2026-06-01", "2026-06-05"),
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event1.id), str(diary.id), db_session)
            await evaluate_event_against_rules(str(event2.id), str(diary.id), db_session)

        await db_session.refresh(event1)
        await db_session.refresh(event2)

        assert event1.entry_id is not None
        assert event2.entry_id is not None
        assert event1.entry_id != event2.entry_id, "different ranges must not group"

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        assert len(entries_result.scalars().all()) == 2
        assert mock_task.delay.call_count == 2

    async def test_null_end_date_groups(self, db_session: AsyncSession):
        """
        Two events from a multi_day=spanning rule that resolve to single-day
        entries (entry_end_date IS NULL) must group. NULL == NULL is treated
        as equal for grouping.
        """
        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_TRIP_CONDITION,
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )

        # Single-day all-day events (Google end-exclusive day after start).
        event1 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip lunch", "2026-06-01", "2026-06-02"),
        )
        event2 = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip dinner", "2026-06-01", "2026-06-02"),
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event1.id), str(diary.id), db_session)
            await evaluate_event_against_rules(str(event2.id), str(diary.id), db_session)

        await db_session.refresh(event1)
        await db_session.refresh(event2)

        assert event1.entry_id == event2.entry_id

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = entries_result.scalars().all()
        assert len(entries) == 1
        assert entries[0].entry_end_date is None
        from datetime import date
        assert entries[0].entry_date == date(2026, 6, 1)

    async def test_manual_entry_is_not_grouped_into(self, db_session: AsyncSession):
        """
        A manual entry with the same date range is left alone — auto events
        must never attach to user-owned manual entries.
        """
        from datetime import date

        from tests.fixtures.factories import make_entry

        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        await _make_rule(
            db_session,
            diary,
            condition=_TRIP_CONDITION,
            options={"recurring": "per_instance", "multi_day": "spanning"},
        )

        manual_entry = await make_entry(
            db_session,
            diary=diary,
            entry_date=date(2026, 6, 1),
        )
        manual_entry.entry_end_date = date(2026, 6, 3)
        # make_entry defaults created_by="manual"; creation_source defaults to "manual".
        assert manual_entry.created_by == "manual"
        await db_session.commit()

        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload=_spanning_payload("Trip flight", "2026-06-01", "2026-06-04"),
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        await db_session.refresh(event)
        await db_session.refresh(manual_entry)

        assert event.entry_id is not None
        assert event.entry_id != manual_entry.id, (
            "auto event must never group into a manual entry"
        )

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = entries_result.scalars().all()
        assert len(entries) == 2
        creation_sources = {e.creation_source for e in entries}
        assert creation_sources == {"manual", "rule"}

    async def test_per_series_path_unaffected(self, db_session: AsyncSession):
        """
        Two recurring instances of a per_series rule continue to share an
        entry via RuleSeriesClaim, and grouping logic does not interfere.

        Regression guard: spanning grouping path must not intercept per_series
        claims. Near-duplicate of test_per_series_second_instance_reuses_entry
        in TestEvaluateEventAgainstRules — that duplication is intentional.
        """
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
            "recurringEventId": "recurring_xyz",
            "start": {"dateTime": "2026-06-01T09:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        }

        event1 = await make_event(
            db_session, diary_id=diary.id, payload=recurring_payload_base
        )
        event2 = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                **recurring_payload_base,
                "start": {"dateTime": "2026-06-08T09:00:00Z"},
            },
        )

        with patch("app.workers.tasks.generate_entry_draft") as mock_task:
            mock_task.delay = MagicMock()
            await evaluate_event_against_rules(str(event1.id), str(diary.id), db_session)
            await evaluate_event_against_rules(str(event2.id), str(diary.id), db_session)

        await db_session.refresh(event1)
        await db_session.refresh(event2)

        assert event1.entry_id == event2.entry_id

        entries_result = await db_session.execute(
            select(Entry).where(Entry.diary_id == diary.id)
        )
        entries = entries_result.scalars().all()
        assert len(entries) == 1, "per_series still produces exactly one entry"

        claims_result = await db_session.execute(
            select(RuleSeriesClaim).where(RuleSeriesClaim.rule_id == rule.id)
        )
        assert len(claims_result.scalars().all()) == 1

        # LLM queued exactly once (per_series reuse path does not regenerate).
        mock_task.delay.assert_called_once_with(str(entries[0].id))
