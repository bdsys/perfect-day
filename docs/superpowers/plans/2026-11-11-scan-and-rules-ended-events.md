# Scan and Rules (25.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entries are created only for calendar events that have ended, cancellations and reschedules are applied to anything not yet published, and one event produces at most one entry.

**Architecture:** Each event stores when it starts and ends (`occurred_at`, new `ends_at`) and whether rules have run for it (new `rules_evaluated_at`). Ingest stores every event but only queues rule evaluation for ended ones; each scan then queues evaluation for stored events that have ended since. Cancelled events from Google go to a new task that deletes the event row and treats its entry according to whether a person has touched it. The rules engine picks the oldest matching rule to decide grouping and records every matching rule against the single entry.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, pytest with testcontainers.

**Spec:** `docs/superpowers/specs/2026-11-11-code-review-decisions-design.md` section 1 (1a, 1b).

## Global Constraints

- "Ended" means the end time has passed. An all-day event has ended when its last day is over in the diary's timezone.
- Rules are evaluated once per event. A new rule does not sweep old events; "apply to past N days" stays the explicit way.
- "Untouched AI draft" means `status = 'draft'` and `body_source` in (`llm`, `fallback`).
- "Oldest rule" is by `created_at`, then `id`.
- A losing per-series rule creates no `RuleSeriesClaim`.
- Background jobs are queued only after their rows are committed (review finding C-4).
- `make test-all` passes at the end.

## Calls made in this plan (not in the spec)

1. **`body_source = 'user'` is added here, not in 25.3.** Today nothing records that a person edited an entry, so "untouched" cannot be told from "edited". A PATCH that changes `body_markdown` or `title`, and a manual create with a body, set `body_source = 'user'`. Section 3 of the spec already calls for this value.
2. **Only rule-created entries are removed automatically.** An entry a person made from the event picker (`creation_source = 'calendar_pick'`) is never soft-deleted by a cancellation or reschedule; the event is just removed from it.
3. **A cancelled event's row is always deleted**, whatever happens to its entry. "Detached" in the spec is read as "no longer on the entry"; keeping a cancelled event as an unattached row would offer it in the picker.
4. **`ends_at` is stored**, so "which events are due" is one indexed query.
5. **Events with no `ends_at`** (non-calendar sources) count as ended.
6. **The one-off clean-up cannot see edits made before this ships**, because edits were not recorded. It soft-deletes drafts (restorable from trash), never hard-deletes.

## Review Focus

- A cancelled event from an incremental sync carries only `id` and `status`, no `start`. It must reach the cancel task and not be dropped by the scan-window filter. (Task 4 test.)
- An event moved from the past to the future after its draft was edited: the text must survive. (Task 5 test.)
- A per-series entry losing one of several instances must stay, be redrafted, and keep its series claim. (Task 4 test.)
- "Apply to past N days" must not create entries for future events, which fall inside `occurred_at >= since`. (Task 3 test.)
- A diary west of UTC: an all-day event must not count as ended at 00:00 UTC of the next day. (Task 1 test.)

---

### Task 1: Event times and the schema

**Files:**
- Modify: `apps/api/app/workers/tz_utils.py`
- Modify: `apps/api/app/models/__init__.py` (`Event`, `Entry` check constraint)
- Create: `apps/api/alembic/versions/0010_event_end_times.py`
- Test: `apps/api/tests/unit/test_tz_utils.py`, `apps/api/tests/integration/test_migration_0010.py`

**Interfaces:**
- Produces: `google_event_times(event: dict, diary_timezone: str) -> tuple[datetime | None, datetime | None]` returning aware `(starts_at, ends_at)`. All-day: local midnight of `start.date`, and local midnight of `end.date` (Google's exclusive end), or start + 1 day when `end.date` is missing. Timed: parsed `start.dateTime`; `end.dateTime`, or the start when missing. Floating times are localised to the diary timezone. Unparseable: `(None, None)`.
- Produces: columns `events.ends_at` and `events.rules_evaluated_at` (both `TIMESTAMPTZ NULL`); index `ix_events_due_for_rules` on `(diary_id, ends_at)` where `entry_id IS NULL AND rules_evaluated_at IS NULL`; `ck_entries_body_source` gains `'user'`.

- [ ] Unit tests for `google_event_times`: all-day `2026-06-01`..`2026-06-02` in `America/Chicago` gives `2026-06-01T05:00Z` and `2026-06-02T05:00Z`; three-day all-day; timed with offset; timed with no end; floating time; garbage gives `(None, None)`.
- [ ] Run, see them fail, implement, see them pass.
- [ ] Migration 0010, in this order: add columns and index; widen the check constraint; backfill `occurred_at` and `ends_at` from `payload` joined to the diary timezone; set `rules_evaluated_at = now()` for every event that is attached or has ended; one-off clean-up (below). Downgrade drops the columns, index and `'user'` (rows with `'user'` become `'llm_hybrid'`).
- [ ] One-off clean-up SQL: entries with `status='draft'`, `body_source IN ('llm','fallback')`, `creation_source='rule'`, `deleted_at IS NULL`, at least one event, and every event `ends_at > now()` get `deleted_at = now()`; their `rule_series_claims` rows are deleted; their events get `entry_id = NULL`.
- [ ] Migration test in its own database on the test Postgres container: upgrade to 0009, insert a past timed event, a future all-day event attached to an untouched rule draft, and a future event attached to a published entry; upgrade to head; assert times, `rules_evaluated_at`, the soft-deleted draft and the untouched published entry.
- [ ] Commit.

### Task 2: One entry per event

**Files:**
- Modify: `apps/api/app/workers/rules.py`
- Test: `apps/api/tests/integration/test_rules_evaluation.py`

**Interfaces:**
- `evaluate_event_against_rules(event_id, diary_id, db)` keeps its signature. It now returns without doing anything when the event has not ended, sets `event.rules_evaluated_at` whenever it evaluates, and creates at most one entry.

- [ ] Rewrite `test_two_rules_matching_same_event`: one entry, two `EntryRuleMatch` rows, one queued draft.
- [ ] New tests: older per-series rule + newer per-instance rule on a recurring event gives one entry and one claim (for the older rule); the reverse order gives one entry and no claim; an event whose `ends_at` is in the future gives no entry and leaves `rules_evaluated_at` NULL; an ended event with no matching rule gets `rules_evaluated_at` set.
- [ ] Run, see them fail.
- [ ] Implement: order rules by `created_at, id`; collect matching rules; the first decides the entry using the existing per-series / spanning / per-instance logic (extracted to a helper); record a match row for every matching rule; queue one draft per entry.
- [ ] Run the rules tests and `tests/integration/test_rule_*.py`; commit.

### Task 3: Ingest stores future events without evaluating them

**Files:**
- Modify: `apps/api/app/workers/tasks.py` (`_ingest_calendar_event`, `_scan_diary`, `_apply_rule_backfill`)
- Test: `apps/api/tests/integration/test_ended_events.py`

**Interfaces:**
- Produces: `async def queue_due_rule_evaluations(diary_id: uuid.UUID) -> int` in `tasks.py`: queues `evaluate_rules_for_event` for each event of the diary with `entry_id IS NULL`, `rules_evaluated_at IS NULL` and `ends_at <= now()` (or `ends_at IS NULL`), and returns how many. `_scan_diary` calls it after the calendar sync, whether or not the sync succeeded.

- [ ] Tests: a future event is stored with `occurred_at`/`ends_at` and queues no evaluation; an ended event queues one; an all-day event stores a non-null `occurred_at` (C-13); `queue_due_rule_evaluations` queues an event whose end has passed and skips future, attached and already-evaluated ones; "apply to past N days" creates no entry for a future event.
- [ ] Run, see them fail, implement, see them pass, commit.

### Task 4: Cancellations

**Files:**
- Create: `apps/api/app/workers/event_changes.py`
- Modify: `apps/api/app/workers/tasks.py` (new task `cancel_calendar_event`), `apps/api/app/workers/calendar_sync.py`
- Test: `apps/api/tests/integration/test_ended_events.py`, `apps/api/tests/unit/test_calendar_sync.py`

**Interfaces:**
- Produces: `async def release_event_from_entry(db: AsyncSession, event: Event) -> uuid.UUID | None` in `event_changes.py`. Takes the event off its entry (`entry_id = NULL`). If the entry is an untouched rule-created AI draft: with no other events it is soft-deleted and its series claims deleted; with other events it is kept and its id returned so the caller queues a redraft after commit. Any other entry is left as it is. Returns `None` when no redraft is needed.
- Produces: Celery task `cancel_calendar_event(external_id: str, diary_id: str)`: finds the event, calls `release_event_from_entry`, deletes the event row, commits, then queues the redraft.

- [ ] Tests: cancelled with no entry leaves no event row; cancelled after an untouched draft soft-deletes the entry and removes its series claim; cancelled after an edited draft (`body_source='user'`) leaves text and entry intact; cancelled on a published entry changes nothing but the event list; cancelled with other events on an untouched draft keeps the entry and queues one redraft; cancelling an unknown id does nothing.
- [ ] Unit test: `sync_calendar` sends a cancelled item that has no `start` to `cancel_calendar_event.delay` and does not ingest it.
- [ ] Run, see them fail, implement, see them pass, commit.

### Task 5: Reschedules

**Files:**
- Modify: `apps/api/app/workers/tasks.py` (`_ingest_calendar_event`)
- Test: `apps/api/tests/integration/test_ended_events.py`

- [ ] Tests: re-ingesting an event with a new time updates `occurred_at` and `ends_at`; moving an event with an untouched draft to the future soft-deletes the draft and clears `rules_evaluated_at`; moving an event with an edited draft to the future leaves the entry and the attachment; moving an evaluated, unattached event to the future clears `rules_evaluated_at`.
- [ ] Run, see them fail, implement (re-ingest updates times; if the event has not ended, call `release_event_from_entry` only when its entry is an untouched rule draft, and clear `rules_evaluated_at` when the event ends up unattached), see them pass, commit.

### Task 6: Edits are recorded; the picker offers ended events only

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py`, `apps/api/app/routers/v1/calendar_events.py`, `apps/web/src/lib/api.ts`
- Test: `apps/api/tests/integration/test_entries.py`, `apps/api/tests/integration/test_picker_endpoint.py`

- [ ] Tests: PATCH changing the body sets `body_source` to `user`; PATCH changing only the date leaves it; manual create with a body gives `user`; the unattached event list omits a future event; `POST .../entries/from-event` on a future event returns 409 `event_not_ended`.
- [ ] Run, see them fail, implement, add `'user'` to the `body_source` type in `api.ts`, see them pass, commit.

### Task 7: Documents and full verification

- [ ] Update `design/06-scan-worker.md` (future events, cancellations, one entry per event), `design/code-review-2026-11.md` (C-6, C-12, C-13), `POC_PHASE2_TODO.md` (25.1).
- [ ] `make test-all`.
- [ ] Commit.

**Not covered by automated tests:** the spec's by-hand check on dev with a real Google calendar (add an event for tomorrow, scan, move it to yesterday, scan).
