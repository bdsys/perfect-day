# Multi-Day Entry Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Save final plan to:** `docs/superpowers/plans/2026-05-28-multi-day-entries.md`

**Goal:** Group calendar events that span the same exact date range into a single multi-day Entry, and surface the date range in the diary timeline and entry detail UI.

**Architecture:** Inline grouping logic in `app/workers/rules.py`'s non-`per_series` branch. When a `per_instance` rule with `multi_day == "spanning"` matches, look up an existing rule-created entry with the exact same `(entry_date, entry_end_date)` (NULL-safe) and attach to it instead of creating a duplicate. Frontend adds a shared `formatDateRange` helper and uses it on the diary list and entry detail pages.

**Tech Stack:** FastAPI / SQLAlchemy async / pytest-asyncio + testcontainers (Postgres + Redis); Next.js 14 App Router + Playwright E2E.

---

## Context — why this change

`entry_end_date` already exists on the Entry model and is wired through the API/CRUD layer; `google_event_to_entry_date()` already returns `(entry_date, entry_end_date)` for multi-day events. But today every calendar event that matches a non-`per_series` rule creates its own Entry — even when several events span the same trip/range. That produces duplicate "Trip" entries when a user has multiple calendar items inside one trip. Wave A item 15 closes that gap by (1) grouping events with identical date ranges into a single Entry at rule-evaluation time, and (2) rendering the resulting `entry_date – entry_end_date` range in the timeline and entry detail UI.

This plan also satisfies the CLAUDE.md instruction to keep `workers/tasks.py` changes minimal — entry creation already lives in `app/workers/rules.py`, not `tasks.py`. We touch `rules.py` only.

## Scope decisions (confirmed with user)

1. **Where:** grouping lives in `app/workers/rules.py`, in the non-`per_series` branch (lines 219-250). Not in `tasks.py`. Not in a separate batch pass.
2. **When:** only when at least one rule matches AND the rule's `multi_day == "spanning"`. `per_day` rules continue to collapse spans to start date as today.
3. **Match key:** exact equality on `(diary_id, entry_date, entry_end_date)` with `NULL == NULL`. Manual entries (`creation_source != 'rule'`) are never grouped into.
4. **`per_series`:** unchanged. Continues to share entries via `RuleSeriesClaim`.
5. **Tier check on reuse:** skipped (mirrors `per_series` reuse path at `rules.py:182-184`).
6. **Draft regeneration on reuse:** queue `generate_entry_draft.delay(entry_id)` so the LLM produces a coherent multi-event draft. Known follow-up to debounce these calls — captured in §15.

## Critical files

**Backend:**
- `apps/api/app/workers/rules.py` — modify the non-`per_series` branch (lines 219-250). Add `and_` to the sqlalchemy import on line 9.
- `apps/api/tests/integration/test_rules_evaluation.py` — append new `TestMultiDayGrouping` class.

**Frontend:**
- `apps/web/src/lib/date.ts` — **new** file. Houses `formatDate` + new `formatDateRange`.
- `apps/web/src/app/diaries/[diaryId]/page.tsx` — delete local `formatDate` (lines 11-18); use `formatDateRange` in `EntryCard` (line 26).
- `apps/web/src/app/entries/[entryId]/page.tsx` — delete local `formatDate` (lines 11-18); use `formatDateRange` in header (line 230).
- `apps/web/e2e/multi-day-entries.spec.ts` — **new** Playwright test.

**Out of scope:** `apps/web/src/app/diaries/[diaryId]/restore/page.tsx` keeps its local `formatDate` duplicate. Out of scope for this PR.

## Background to read first (do this BEFORE writing code)

| File | Why |
|---|---|
| `apps/api/app/workers/rules.py` (full file) | Understand the locking pattern at lines 170-218 (`per_series`). Your grouping query mirrors it. |
| `apps/api/app/workers/tz_utils.py:11-54` | `google_event_to_entry_date()` returns `(entry_date, entry_end_date)`. Returns `entry_end_date=None` for single-day. Google all-day end is exclusive (subtract 1 day). |
| `apps/api/app/models/__init__.py:272-364` | Entry + Event models. **No** `(diary_id, entry_date, entry_end_date)` uniqueness constraint exists — race safety relies on `with_for_update` + single transaction. |
| `apps/api/tests/integration/test_rules_evaluation.py` | Canonical test style. Note `wire_worker_db` autouse fixture pattern at lines 21-38. |
| `apps/api/tests/fixtures/factories.py:57-77` | `make_entry` defaults `created_by="manual"`. Test 4 relies on this. |
| `apps/web/src/app/diaries/[diaryId]/page.tsx:11-44` | `EntryCard` and duplicated `formatDate`. |
| `apps/web/src/app/entries/[entryId]/page.tsx:11-18, 228-231` | Header date rendering. |
| `apps/web/src/lib/api.ts:158` | `entry_end_date: string \| null` already in the type — no type change needed. |
| `apps/web/e2e/golden-path.spec.ts` | Playwright + auth flow conventions. Login page lives at `/login`; selectors `#email`, `#password`, `button[type=submit]`. |

---

## Task list (sequential)

| # | Type | What |
|---|---|---|
| 1 | Test (RED) | Same-range spanning events group into one entry |
| 2 | Test (regression guard) | Different-range spanning events do NOT group |
| 3 | Test (RED) | NULL == NULL (single-day spanning) groups |
| 4 | Test (regression guard) | Manual entries are NOT grouped into |
| 5 | Test (regression guard) | `per_series` path is unchanged |
| 6 | Implementation (GREEN) | Modify `rules.py` non-`per_series` branch |
| 7 | Verification | Run rules-evaluation file + commit |
| 8 | Test (RED) | Playwright: diary + entry detail render date range |
| 9 | Implementation (GREEN) | Extract `formatDateRange`; wire into both pages |
| 10 | Verification | Run `make test-all` + commit |

---

## Task 1 — Same-range spanning events group into one entry

**Files:**
- Modify: `apps/api/tests/integration/test_rules_evaluation.py` (append at end)

- [ ] **Step 1: Write the failing test**

Append at end of `test_rules_evaluation.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping::test_same_range_spanning_events_group -q
```

Expected FAIL output:
```
AssertionError: both events must share one entry
assert UUID(...) == UUID(...)
```

(Today, two distinct entries get created; `event1.entry_id != event2.entry_id`.)

---

## Task 2 — Different-range spanning events do NOT group (regression guard)

**Files:**
- Modify: `apps/api/tests/integration/test_rules_evaluation.py` (append inside `TestMultiDayGrouping`)

- [ ] **Step 1: Write the test**

```python
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
```

- [ ] **Step 2: Run the test (passes today as a baseline)**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping::test_different_range_spanning_events_do_not_group -q
```

Expected: PASS today (no grouping happens, so it trivially passes). After Task 6 implementation, it must still PASS — this guards against an over-broad `WHERE` clause in your grouping query.

---

## Task 3 — NULL == NULL (single-day spanning) groups

**Files:**
- Modify: `apps/api/tests/integration/test_rules_evaluation.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping::test_null_end_date_groups -q
```

Expected FAIL:
```
assert UUID(...) == UUID(...)
AssertionError
```

---

## Task 4 — Manual entries are NOT grouped into (regression guard)

**Files:**
- Modify: `apps/api/tests/integration/test_rules_evaluation.py`

- [ ] **Step 1: Write the test**

```python
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
```

- [ ] **Step 2: Run the test (passes today as a baseline)**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping::test_manual_entry_is_not_grouped_into -q
```

Expected: PASS today (no grouping at all). After Task 6 it must still PASS — guards the `creation_source == 'rule'` filter.

---

## Task 5 — `per_series` path is unchanged (regression guard)

**Files:**
- Modify: `apps/api/tests/integration/test_rules_evaluation.py`

- [ ] **Step 1: Write the test**

`_STANDUP_CONDITION` is already defined earlier in the same file — reuse it.

```python
    async def test_per_series_path_unaffected(self, db_session: AsyncSession):
        """
        Two recurring instances of a per_series rule continue to share an
        entry via RuleSeriesClaim, and grouping logic does not interfere.
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
```

- [ ] **Step 2: Run all five new tests**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping -q
```

Expected RED baseline: **Tests 1 and 3 FAIL. Tests 2, 4, 5 PASS.** This is the correct red baseline before implementation.

---

## Task 6 — Implementation: modify `rules.py`

**Files:**
- Modify: `apps/api/app/workers/rules.py`

- [ ] **Step 1: Update the sqlalchemy import (line 9)**

```python
from sqlalchemy import and_, select
```

- [ ] **Step 2: Replace the non-`per_series` branch (lines 219-250)**

Replace the entire `else:` block with:

```python
        else:
            # ----------------------------------------------------------------
            # per_instance / multi_day path. For multi_day == "spanning",
            # group into an existing rule-created entry that has the EXACT
            # same (entry_date, entry_end_date) range — including NULL == NULL.
            # Manual entries are never grouped into.
            # ----------------------------------------------------------------
            use_end_date = (
                entry_end_date if options.get("multi_day") == "spanning" else None
            )

            existing_entry: Entry | None = None
            if options.get("multi_day") == "spanning":
                # NULL-safe equality on entry_end_date:
                #   - if use_end_date is None: match rows where entry_end_date IS NULL
                #   - else: match rows where entry_end_date == use_end_date
                if use_end_date is None:
                    end_predicate = Entry.entry_end_date.is_(None)
                else:
                    end_predicate = Entry.entry_end_date == use_end_date

                existing_result = await db.execute(
                    select(Entry)
                    .where(
                        and_(
                            Entry.diary_id == diary_uuid,
                            Entry.entry_date == entry_date,
                            end_predicate,
                            Entry.creation_source == "rule",
                            Entry.deleted_at.is_(None),
                        )
                    )
                    .order_by(Entry.created_at.asc())
                    .limit(1)
                    .with_for_update()
                )
                existing_entry = existing_result.scalar_one_or_none()

            if existing_entry is not None:
                # Reuse — skip tier check (no new entry being created).
                entry_id_to_use = existing_entry.id
                log.info(
                    "rules_event_grouped",
                    entry_id=str(entry_id_to_use),
                    event_id=event_id,
                    diary_id=diary_id,
                    rule_id=str(rule.id),
                    entry_date=str(entry_date),
                    entry_end_date=str(use_end_date) if use_end_date else None,
                )
                # Re-queue draft regeneration so the LLM produces a coherent
                # multi-event entry. Known follow-up: dedupe these calls at
                # the LLM-task layer so a scan window with N grouped events
                # does not produce N regenerations.
                try:
                    generate_entry_draft.delay(str(entry_id_to_use))
                except Exception:
                    log.exception(
                        "evaluate_rules_llm_queue_failed",
                        entry_id=str(entry_id_to_use),
                    )
            else:
                ok, reason = await try_enforce_entry_tier_limit(
                    owner_user_id=user.id,
                    source="auto",
                    db=db,
                    owner_subscription_tier=user.subscription_tier,
                )
                if not ok:
                    log.warning(
                        "evaluate_rules_tier_limit",
                        rule_id=str(rule.id),
                        reason=reason,
                    )
                    continue

                new_entry = Entry(
                    diary_id=diary_uuid,
                    entry_date=entry_date,
                    entry_end_date=use_end_date,
                    status="draft",
                    created_by="auto",
                    creation_source="rule",
                )
                db.add(new_entry)
                await db.flush()
                entry_id_to_use = new_entry.id
```

**Why this is safe:**
- `use_end_date` is computed once at the top, so `per_day` rules continue to ignore `entry_end_date` (always `None`) and skip the grouping lookup entirely. Only spanning rules participate.
- `with_for_update()` locks the candidate row so two concurrent rule evaluations on the same range cannot both decide "no existing entry" and each create one.
- `order_by(Entry.created_at.asc()).limit(1)` is defensive — if the constraint is ever violated by a race that slipped past the lock, we deterministically pick the oldest entry.
- `creation_source == 'rule'` excludes manual and `calendar_pick` entries (Test 4 covers manual; `calendar_pick` is a user-curated single event so should not group either — exclusion is conservative).
- Tier check is skipped on reuse — mirrors `per_series` reuse path at lines 182-184.
- `new_entry` stays `None` on the reuse path, so the existing block at lines 272-279 (`if new_entry is not None: generate_entry_draft.delay(...)`) does nothing for grouping. The grouping path queues regeneration explicitly — keeps the two flows readable.

- [ ] **Step 3: Run the new tests to verify GREEN**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py::TestMultiDayGrouping -q
```

Expected: **5 passed**.

- [ ] **Step 4: Run the full file to confirm no regressions**

```bash
cd apps/api && ../../apps/api/.venv/bin/pytest \
  tests/integration/test_rules_evaluation.py -q
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/rules.py \
        apps/api/tests/integration/test_rules_evaluation.py

git commit -m "$(cat <<'EOF'
feat(workers): group multi-day spanning events into existing entry

When a per_instance rule with multi_day == "spanning" matches an
event, attach it to an existing rule-created entry with the same
(entry_date, entry_end_date) range instead of creating a duplicate.
NULL-safe on entry_end_date so single-day spanning events also group.

Manual entries are never grouped into. The per_series path is
unchanged. Tier check is skipped on reuse. Draft regeneration is
re-queued on each group-in so the LLM produces a coherent multi-event
entry.

Known follow-up: dedupe regeneration calls at the LLM-task layer.
EOF
)"
```

---

## Task 7 — Frontend test (RED): Playwright multi-day spec

**Files:**
- Create: `apps/web/e2e/multi-day-entries.spec.ts`

- [ ] **Step 1: Write the failing test**

The `POST /v1/diaries/{diary_id}/entries` endpoint accepts `entry_end_date` directly (verified at `apps/api/app/routers/v1/entries.py:243-275`). No PATCH workaround needed.

```typescript
import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const email = 'e2e-multi-day@example.com'
const password = 'Password1!'

test.describe('Multi-day entry date range', () => {
  let diaryId = ''
  let entryId = ''

  test.beforeAll(async () => {
    const ctx = await playwrightRequest.newContext()

    // Register or accept conflict (user may already exist from previous run).
    await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })

    const loginRes = await ctx.post(`${API}/v1/auth/login`, {
      data: { email, password },
    })
    const { access_token } = await loginRes.json()
    const auth = { Authorization: `Bearer ${access_token}` }

    const diaryRes = await ctx.post(`${API}/v1/diaries`, {
      headers: auth,
      data: { name: 'Multi-Day Diary', timezone: 'UTC' },
    })
    const diary = await diaryRes.json()
    diaryId = diary.id

    const entryRes = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
      headers: auth,
      data: { entry_date: '2026-06-01', entry_end_date: '2026-06-03' },
    })
    const entry = await entryRes.json()
    entryId = entry.id

    await ctx.dispose()
  })

  test('diary timeline shows date range', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')

    await page.goto(`/diaries/${diaryId}`)

    const card = page.locator('.entry-card').first()
    await expect(card).toBeVisible()
    await expect(card).toContainText('2026')
    // En-dash separator between start and end.
    await expect(card).toContainText('–')
  })

  test('entry detail page shows date range in header', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')

    await page.goto(`/entries/${entryId}`)

    await expect(page.locator('text=–').first()).toBeVisible()
    await expect(page.getByText(/2026/).first()).toBeVisible()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/web && npx playwright test e2e/multi-day-entries.spec.ts
```

Expected FAIL:
```
Error: expect(locator).toContainText('–')
Locator: locator('.entry-card').first()
Received string: "Monday, June 1, 2026"
```

(Today's `EntryCard` renders only `entry_date` via `formatDate`, no en-dash.)

---

## Task 8 — Frontend implementation: extract `formatDateRange`

**Files:**
- Create: `apps/web/src/lib/date.ts`
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

- [ ] **Step 1: Create `apps/web/src/lib/date.ts`**

```typescript
export function formatDate(d: string): string {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Render an entry's date or date range. If endDate is null/undefined or
 * equal to startDate, returns just the formatted start date. Otherwise
 * returns "<start> – <end>" with an en-dash separator.
 */
export function formatDateRange(
  startDate: string,
  endDate: string | null | undefined,
): string {
  if (!endDate || endDate === startDate) {
    return formatDate(startDate)
  }
  return `${formatDate(startDate)} – ${formatDate(endDate)}`
}
```

> Note: keeping `'long'` weekday/month to match the existing `formatDate` exactly. No incidental UI churn.

- [ ] **Step 2: Modify `apps/web/src/app/diaries/[diaryId]/page.tsx`**

1. Delete the local `formatDate` function (lines 11-18).
2. Add to imports near the top:
   ```typescript
   import { formatDateRange } from '@/lib/date'
   ```
3. Change line 26 inside `EntryCard`:
   - From: `<div className="entry-date">{formatDate(entry.entry_date)}</div>`
   - To:   `<div className="entry-date">{formatDateRange(entry.entry_date, entry.entry_end_date)}</div>`

- [ ] **Step 3: Modify `apps/web/src/app/entries/[entryId]/page.tsx`**

1. Delete the local `formatDate` function (lines 11-18).
2. Add to imports:
   ```typescript
   import { formatDateRange } from '@/lib/date'
   ```
3. Change line 230 in the header:
   - From: `<span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{formatDate(entry.entry_date)}</span>`
   - To:   `<span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{formatDateRange(entry.entry_date, entry.entry_end_date)}</span>`

- [ ] **Step 4: Run the Playwright test to verify GREEN**

```bash
cd apps/web && npx playwright test e2e/multi-day-entries.spec.ts
```

Expected: **2 passed**.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/date.ts \
        apps/web/src/app/diaries/[diaryId]/page.tsx \
        apps/web/src/app/entries/[entryId]/page.tsx \
        apps/web/e2e/multi-day-entries.spec.ts

git commit -m "$(cat <<'EOF'
feat(web): render multi-day date range on diary timeline + entry detail

Adds shared formatDateRange helper at apps/web/src/lib/date.ts that
renders "<start> – <end>" when entry_end_date is set and differs from
entry_date, falling back to the single-date string otherwise.

EntryCard on the diary page and the entry detail header now use the
helper. Adds a Playwright spec asserting the en-dash and year render.

The third formatDate duplicate in restore/page.tsx is left for a
later consolidation pass.
EOF
)"
```

---

## Verification

Run the project's standard suite per CLAUDE.md instruction "Run make test-all":

```bash
make test-all
```

This runs (per `Makefile:87-95`):
1. `make lint`
2. `make typecheck`
3. `make test` (pytest unit + integration)
4. `make test-e2e` (Playwright)

All four targets must pass. Expected outcomes:
- New `TestMultiDayGrouping` class: 5 tests pass.
- Existing `test_rules_evaluation.py` tests: still pass.
- New `multi-day-entries.spec.ts`: 2 tests pass.
- All other Playwright specs: still pass (no shared state changes).

### Manual smoke check (optional, ~3 minutes)

1. Boot the stack: `make dev` (or whatever the project's dev command is — see Makefile).
2. In the web UI, create a manual entry with `entry_date = 2026-06-01`, `entry_end_date = 2026-06-03`.
3. Open the diary page. The card should read `Monday, June 1, 2026 – Wednesday, June 3, 2026` (or localized equivalent with en-dash).
4. Click into the entry detail page. The header should also show the range.
5. Single-date entries should still render as a single date — no en-dash, no trailing dash.

---

## Known follow-ups (do NOT implement in this PR)

1. **LLM regeneration debounce.** Each grouped event currently triggers a draft regeneration. Within a single scan window with N events grouping into one entry, that's N regenerations. Add a debounce at the LLM-task layer keyed on `entry_id` with a short coalescing window (~30s).
2. **`restore/page.tsx` `formatDate` duplicate.** Out of scope here. Consolidate when next touching that file.
3. **Optional partial unique index** on `(diary_id, entry_date, COALESCE(entry_end_date, '9999-12-31'))` filtered by `creation_source = 'rule' AND deleted_at IS NULL`. Defense in depth against the race even with `with_for_update`. Requires migration; defer until grouping is stable.

---

## Self-review

- **Spec coverage:** worker grouping (Tasks 1-6), timeline UI (Tasks 7-8), TDD discipline (RED before GREEN at Tasks 1, 3, 7), regression guards for `per_series` and manual entries (Tasks 2, 4, 5). All requirements covered.
- **Placeholders:** none. All test code, implementation code, and commands are concrete.
- **Type consistency:** `entry_end_date` is `date | None` server-side and `string | null` client-side throughout; `formatDateRange(startDate: string, endDate: string | null | undefined): string` matches the API type at `apps/web/src/lib/api.ts:158`. `Entry.entry_end_date.is_(None)` and `Entry.entry_end_date == use_end_date` are both valid SQLAlchemy 2.0 idioms.
- **Critical-file paths:** verified to exist via direct `Read`/`grep`. `Makefile:87-95` `test-all` target confirmed. `make_entry` factory default of `created_by="manual"` confirmed at `factories.py:71`. POST entry endpoint accepts `entry_end_date` confirmed at `entries.py:265-266`.
