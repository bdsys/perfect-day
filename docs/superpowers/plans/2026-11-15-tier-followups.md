# Tier enforcement follow-ups (PoC 18.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** close the nine parent-visible gaps item 18's review left, and apply Andrew's decisions D1–D6: free coalesced redrafts, paid backfill with a 90-day Free look-back, calendar-pick drafts that follow their event.

**Architecture:**
- The plan math stays in `services/tier.py`.
- Every new refusal uses the R3 `tier_limit` shape with a new `"backfill"` source.
- Free redrafts are decided at run time in `workers/llm.py`. Coalescing is a Redis `SET NX` plus a delayed Celery task.
- Lost drafts are swept by a beat task keyed on a new `llm_generations.reservation_key` (migration 0018).
- The web gets one `limitMessage` mapper used by every action that can be refused.

**Tech Stack:** FastAPI 0.162.5, SQLAlchemy async, Alembic, Celery/Redis, Postgres (testcontainers), Next.js 15, Jest, Playwright; Python 3.12 (uv venv).

**Spec:** `docs/superpowers/specs/2026-11-15-tier-followups-design.md` (authority). Parent spec: `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md`.

## Global Constraints

- Wire shape (R3): 403 `{"detail": {"code": "tier_limit", "details": {limit, current, source, required_tier, resets_on?}}}`.
- Guard order (R2): access → `ensure_diary_writable` → plan limit.
- An unknown tier is treated as free. Plan math lives only in `services/tier.py`. The plan that counts is the diary owner's.
- Free: `backfill=False`, `scan_past_days=90`. Plus/Family: `backfill=True`, `scan_past_days=None`.
- `future_days` is never limited.
- Copy, exact:
  - `BACKFILL_LIMIT_NOTE = "Backfill is on Plus and Family. Free scans look back 90 days."`;
  - read-only backfill error `"This diary is read-only on your plan, so the backfill didn't run."`;
  - unknown source `"Your plan doesn't include this."`;
  - no-plan fallbacks `"Your plan: 1 diary."` and `"This diary is read-only on your plan. Upgrade, or delete another diary, to use it again."`;
  - reset separator `" · resets {date}"`.
- Coalescing: Redis key `redraft_pending:{entry_id}`, `SET … NX EX 900`, task `countdown=600`. Log events `redraft_coalesced` and `redraft_free`.
- Sweeper: `app.workers.beat_tasks.release_lost_ai_drafts`, every 10 minutes, releases spends older than 30 minutes.
- Text a person wrote is never changed by a background job. Facts from source data only.
- TDD: every change starts with a failing test.
- Stage explicit paths only; never `git add -A` or `-a`. Never `pkill -f`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Test commands:
  - API: `cd apps/api && .venv/bin/pytest <path> -q`;
  - web: `cd apps/web && npx jest <path>`;
  - full suite: `make test`, `make test-all`.

## Review Focus

1. **A Plus owner downgraded to Free with a backfill already pending.** The worker must end it `failed` with `BACKFILL_LIMIT_NOTE`, not run it, and not spin forever (Task 2).
2. **Viewers still get `plan: null`** (F5). The new plan fields must not reach a viewer (Task 1).
3. **Deploy-time state.**
   - Spends written before 0018 must never be swept (`sweepable=false`), so nobody gets a month of drafts refunded (Task 3).
   - Celery messages queued before the deploy carry no `coalesced` kwarg and must run as plain background drafts (Task 5).
4. **Beat scans and the diary's first scan send no `past_days`.** They must stay allowed on Free (Task 2).
5. **A `tier_limit` with a source the web doesn't know** shows "Your plan doesn't include this.", never the raw code (Task 7).

---

### Task 1: Plan limits for backfill

**Files:**
- Modify: `apps/api/app/services/tier.py`, `apps/api/app/services/plan_view.py`
- Test: `apps/api/tests/unit/test_plans.py`, `apps/api/tests/integration/test_plan_endpoints.py`

**Interfaces:**
- Produces:
  - `PlanLimits` gains fields `backfill: bool` and `scan_past_days: int | None`. Keep them after the existing three, so positional construction still lists diaries, AI drafts and photos first.
  - `LimitSource` includes `"backfill"`.
  - `ensure_backfill_allowed(tier: str, past_days: int | None) -> None` raises `tier_limit_error(source="backfill", limit=90, current=past_days or 0, needed=…)`:
    - `past_days=None` means a date-range backfill, refused when `backfill=False`;
    - a number is refused when `scan_past_days` is set and `past_days > scan_past_days`.
  - `BACKFILL_LIMIT_NOTE: str` (the same copy as Global Constraints) for worker messages.
  - `OwnerPlanOut` and `MyPlanOut` gain `backfill: bool` and `scan_past_days_max: int | None`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_plans.py`:
  - `test_limits_per_plan` becomes `PlanLimits(1, 5, 1, False, 90)`, `PlanLimits(2, None, None, True, None)` and `PlanLimits(4, None, None, True, None)`.
  - `test_required_tier_backfill`: `required_tier("backfill", 91) == "plus"`.
  - `test_backfill_allowed`:
    - `ensure_backfill_allowed("plus", None)` and `("free", 90)` don't raise;
    - `("free", None)` raises, with `exc.detail == {"code": "tier_limit", "details": {"limit": 90, "current": 0, "source": "backfill", "required_tier": "plus"}}`;
    - `("free", 365)` raises with `current == 365`;
    - `("tier1", 91)` raises (unknown is free).

  In `tests/integration/test_plan_endpoints.py`:
  - `test_owner_plan_backfill_fields`: a Free owner's `GET /v1/diaries/{id}` has `plan.backfill is False` and `plan.scan_past_days_max == 90`; for Plus, `True` and `None`. `GET /v1/auth/me/plan` has the same.
  - The existing viewer test still has `plan is None` (extend it if it doesn't already assert that).
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/unit/test_plans.py tests/integration/test_plan_endpoints.py -q`. Expected: FAIL (new fields, `ensure_backfill_allowed`, the backfill source).
- [ ] **Step 3: Implement** in `tier.py`: `_limit_for_source("backfill")` returns `scan_past_days`. Fill the fields in `plan_view.owner_plan_out` and `my_plan_out` from `limits_for`.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then `make test`. Expected: green, apart from any test that builds `PlanLimits` positionally (fix those to the new shape).
- [ ] **Step 5: Commit** `feat(tier): backfill and 90-day look-back in plan limits` (stage `apps/api/app/services/tier.py apps/api/app/services/plan_view.py apps/api/tests/unit/test_plans.py apps/api/tests/integration/test_plan_endpoints.py` plus any test fixed in Step 4, by path).

### Task 2: Backfill guards in routes and workers (18.1-c, 18.1-i API)

**Files:**
- Modify: `apps/api/app/routers/v1/scan.py` (`trigger_scan` at `/diaries/{id}/scan/run`, and the backfill POST), `apps/api/app/routers/v1/rules.py` (`apply_rule`), `apps/api/app/workers/tasks.py` (`_backfill_diary`, `_apply_rule_backfill`)
- Test: `apps/api/tests/integration/test_backfill.py`, `test_backfill_worker.py`, `test_rule_backfill.py`, `test_read_only_diaries.py`

**Interfaces:**
- Consumes: `ensure_backfill_allowed`, `BACKFILL_LIMIT_NOTE` (Task 1).
- Rules routes are owner-only (`rules.py:130-140`), and Scan requires the owner, so the caller's own `user.subscription_tier` is the owner's tier.

- [ ] **Step 1: Write the failing tests:**
  - `test_free_backfill_refused`: Free owner, `POST /v1/diaries/{id}/scan/backfill` → 403 with `detail.details.source == "backfill"` and `required_tier == "plus"`. No `BackfillRun` row is created, and the task is not queued. A Plus owner gets the existing success.
  - `test_free_scan_past_days_capped`, with `scan_diary.delay` patched:
    - Free `POST …/scan/run` with `{"past_days": 365}` → 403 backfill, `current == 365`;
    - `{"past_days": 90}` → 200;
    - no body → 200;
    - `{"future_days": 365}` → 200;
    - Plus with `{"past_days": 365}` → 200.
  - `test_free_rule_apply_capped`: Free `POST /v1/rules/{id}/apply` with `days=91` → 403 backfill; `days=90` → `{"queued": True}`.
  - `test_read_only_wins_over_backfill_limit` (R2): a Free owner's read-only diary gets `source == "diary_read_only"` on `/scan/backfill`.
  - `test_read_only_backfill_run_finishes`: a pending run on a read-only diary → after `_backfill_diary`, `status == "failed"`, `completed_at` is set, and `error` is the read-only copy.
  - `test_cancelled_backfill_run_stays_cancelled_when_read_only`: the same setup with status `cancelled` → still `cancelled`, and `error` is unchanged.
  - `test_downgraded_owner_backfill_run_fails` (Review Focus 1): a pending run, owner set to free → `failed` with `error == BACKFILL_LIMIT_NOTE`. Google is never called (patch `run_backfill` and assert it wasn't awaited).
  - `test_downgraded_owner_rule_apply_skipped`: a Free owner, `_apply_rule_backfill(rule_id, 91)` → no rule evaluation, plus log `backfill_not_on_plan`. 90 runs as today.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_backfill.py tests/integration/test_backfill_worker.py tests/integration/test_rule_backfill.py tests/integration/test_read_only_diaries.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement:**
  - Route guards go after `ensure_diary_writable` (R2). In `trigger_scan`, call the guard only when `body` and `body.past_days` are set.
  - In `_backfill_diary`:
    - move the read-only skip and the new plan check after the `status != "pending"` guard (`tasks.py:913`), inside the same session;
    - set `failed`, `completed_at` and the message;
    - return.
  - In `_apply_rule_backfill`: load the owner's tier and return early with the log when `days > scan_past_days`.
- [ ] **Step 4: Run** the Step 2 command (expected: PASS), then `make test` (expected: green).
- [ ] **Step 5: Commit** `feat(backfill): Plus/Family only; Free looks back 90 days; read-only runs finish`.

### Task 3: Sweep drafts lost to a killed worker (18.1-d)

**Files:**
- Create: `apps/api/alembic/versions/0018_reservation_keys.py`
- Modify: `apps/api/app/models/__init__.py` (`LLMGeneration.reservation_key`, `AiDraftSpend.sweepable`), `apps/api/app/services/ai_drafts.py` (`reserve_ai_draft` sets `sweepable=True`; add `release_lost_ai_drafts`), `apps/api/app/workers/llm.py` (generation rows carry the key), `apps/api/app/workers/beat_tasks.py`, `apps/api/app/workers/celery_app.py`
- Test: `apps/api/tests/integration/test_ai_drafts.py`, new `apps/api/tests/integration/test_migration_0018.py`

**Interfaces:**
- Produces:
  - `LLMGeneration.reservation_key: str | None` (Text, indexed `ix_llm_generations_reservation_key`).
  - `AiDraftSpend.sweepable: bool` (`server_default=false`, NOT NULL).
  - `async def release_lost_ai_drafts(db: AsyncSession, now: datetime | None = None) -> int` in `services/ai_drafts.py`, returning the number released.
  - The beat task `app.workers.beat_tasks.release_lost_ai_drafts`, scheduled every `timedelta(minutes=10)`.
- Rule: a spend is released when all of these hold:
  - `sweepable`;
  - `created_at < now - 30 min`;
  - `entry_id IS NOT NULL`;
  - no `llm_generations` row has `reservation_key = spend.key` and `status='success'`.

- [ ] **Step 1: Write the failing tests** in `test_ai_drafts.py`. Each one seeds rows directly and calls `release_lost_ai_drafts(db, now=…)`:
  - `test_sweeper_releases_old_orphan`: a sweepable spend 31 minutes old, no generation → released, returns 1.
  - `test_sweeper_keeps_fresh_spend`: 29 minutes old → kept.
  - `test_sweeper_keeps_spend_with_its_success`: a success generation with `reservation_key = spend.key` → kept.
  - `test_sweeper_releases_killed_draft_after_retry_succeeds`: S1 (40 minutes old, no generation) and S2 (35 minutes old, success with key S2) on the same entry → S1 released, S2 kept.
  - `test_sweeper_keeps_unsweepable_and_null_entry`: `sweepable=False` → kept; `entry_id=None` → kept.
  - `test_successful_draft_records_its_reservation_key`: run `generate_draft_for_entry(..., reservation_key="k1")` with the mocked provider → the success `LLMGeneration.reservation_key == "k1"`, and `AiDraftSpend.sweepable is True`.

  In `test_migration_0018.py` (follow `test_migration_0017.py`): `test_existing_spends_not_sweepable`. Upgrade to 0017, insert a spend, upgrade to 0018 → `sweepable is False`. The downgrade drops both columns.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_ai_drafts.py tests/integration/test_migration_0018.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The migration's `down_revision` is 0017's revision id. Set `reservation_key=reservation_key` on the `LLMGeneration` rows written while a reservation is held (`llm.py` near 860 and 884). Use one `DELETE … WHERE … AND NOT EXISTS (…)` statement, and log `released_lost_ai_drafts count=N`.
- [ ] **Step 4: Run** the Step 2 command (expected: PASS), then `make test` (expected: green).
- [ ] **Step 5: Commit** `feat(ai-drafts): give back drafts lost to a killed worker (migration 0018)`.

### Task 4: Automatic redrafts are free (18.1-h, run time)

**Files:**
- Modify: `apps/api/app/workers/llm.py` (`generate_draft_for_entry`)
- Test: `apps/api/tests/integration/test_ai_drafts.py`

**Interfaces:**
- Consumes: `reservation_key` on generation rows (Task 3). A free redraft writes `reservation_key=None`.
- Rule: skip `reserve_ai_draft` when `trigger == "background"` and a `LLMGeneration` row exists for the entry with `status == 'success'`. Log `redraft_free entry_id=…`.

- [ ] **Step 1: Write the failing tests:**
  - `test_background_redraft_free_at_limit`: a Free owner at 5 of 5, and an entry with a prior success generation and machine-only text → the background draft awaits the provider, the spend count stays 5, and the new success row has `reservation_key is None`.
  - `test_first_background_draft_blocked_at_limit`: the same, but no prior success → the provider is not awaited, and the `ai_draft_limit` marker row is written (today's behaviour).
  - `test_regenerate_counts_even_with_prior_draft`: `trigger="regenerate"`, at 4 of 5, with a prior success → the spend count becomes 5.
  - `test_free_redraft_keeps_parents_text`: the prior success exists, but `body_source` is the parent's (not `llm`/`fallback`) → no text overwrite (the existing `write_in_place` rule), and no spend.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_ai_drafts.py -q`. Expected: the first and fourth FAIL; the second and third pass (guards).
- [ ] **Step 3: Implement.** Check for the prior success inside the existing session before `reserve_ai_draft`. The release paths must not try to release a free redraft's key: it was never reserved, and `release_ai_draft` on a missing key is a no-op, so confirm that and leave it.
- [ ] **Step 4: Run** the Step 2 command (expected: PASS), then `make test` (expected: green).
- [ ] **Step 5: Commit** `feat(ai-drafts): automatic redrafts of an AI-drafted entry are free`.

### Task 5: Coalesce redrafts (18.1-h, queue time)

**Files:**
- Modify: `apps/api/app/workers/tasks.py` (new `queue_redraft`; `generate_entry_draft` gains `coalesced`; call sites near 412 and 470), `apps/api/app/workers/rules.py` (`_entry_for_event` third flag; call site 171)
- Test: new `apps/api/tests/integration/test_redraft_coalescing.py`, `apps/api/tests/integration/test_rules_evaluation.py`

**Interfaces:**
- Produces:
  - `def queue_redraft(entry_id: uuid.UUID) -> None` in `workers/tasks.py`.
  - `generate_entry_draft(self, entry_id: str, trigger: str = "background", coalesced: bool = False)`; `_generate_entry_draft(..., coalesced: bool = False)`.
  - `_entry_for_event` returns `(entry_id, needs_draft, joined_without_draft, joined_existing)`. `joined_existing` is True only for the spanning join (`rules.py:278`).
- Behaviour:
  - `SET redraft_pending:{id} 1 NX EX 900`. If it was set: `generate_entry_draft.apply_async((str(id),), {"coalesced": True}, countdown=600)`. Otherwise log `redraft_coalesced`.
  - A Redis exception falls back to `generate_entry_draft.delay(str(id))`.
  - Only a coalesced task deletes the key, before it reads the entry.

- [ ] **Step 1: Write the failing tests** (patch `generate_entry_draft`, real Redis from the test stack):
  - `test_two_redrafts_in_window_queue_one`: `queue_redraft(e)` twice → `apply_async` called once, with `countdown=600` and `{"coalesced": True}`.
  - `test_coalesced_task_clears_key`: after `_generate_entry_draft(str(e), coalesced=True)` (draft internals patched), the key is gone, and the next `queue_redraft` queues again.
  - `test_regenerate_does_not_clear_key`: `_generate_entry_draft(str(e), "regenerate")` → the key is still present.
  - `test_redis_down_queues_directly`: patch `get_redis` to raise → `delay` is called once.
  - `test_old_message_without_coalesced_runs` (Review Focus 3): `generate_entry_draft.run(str(e))` with the old two-argument call → runs as a background draft.
  - `test_cancel_and_reschedule_use_queue_redraft`: `_cancel_calendar_event` and the reschedule path (`tasks.py` near 412) on a two-event rule entry → `queue_redraft` called, `generate_entry_draft.delay` not called.
  - In `test_rules_evaluation.py`, `test_spanning_join_coalesces_new_entry_does_not`: a new entry → `generate_entry_draft.delay` called; an event joining an existing spanning entry → `queue_redraft` called instead.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_redraft_coalescing.py tests/integration/test_rules_evaluation.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Update every existing caller and test of `_entry_for_event` for the fourth value.
- [ ] **Step 4: Run** the Step 2 command, plus `tests/integration/test_ended_events.py` (expected: PASS), then `make test` (expected: green; existing tests that assert `generate_entry_draft.delay` at the cancel and reschedule sites move to `queue_redraft`).
- [ ] **Step 5: Commit** `feat(redraft): coalesce automatic redrafts to one per entry per 10 minutes`.

### Task 6: Untouched calendar-pick drafts follow their event (18.1-g)

**Files:**
- Modify: `apps/api/app/workers/event_changes.py`
- Test: `apps/api/tests/integration/test_ended_events.py`

**Interfaces:**
- Produces: `is_untouched_auto_draft(entry: Entry) -> bool`, used only by `release_event_from_entry`. It is true when all of these hold:
  - `status == "draft"`;
  - `deleted_at is None`;
  - `creation_source in ("rule", "calendar_pick")`;
  - `body_source in MACHINE_ONLY_BODY_SOURCES`, or (`body_source is None` and the body is empty).

  `is_untouched_rule_draft` is unchanged, and so is its caller `_release_from_untouched_draft` (`tasks.py:483-492`).

- [ ] **Step 1: Write the failing tests** (follow `test_cancelled_after_an_untouched_draft_removes_the_entry`):
  - `test_cancelled_calendar_pick_draft_is_trashed`: a `calendar_pick` entry with `body_source="llm"` and one event → cancel → `deleted_at` is set, and no redraft is queued.
  - `test_cancelled_empty_calendar_pick_is_trashed`: `body_source=None`, empty body → trashed.
  - `test_cancelled_edited_calendar_pick_kept`: `body_source` is the parent's → kept, text unchanged.
  - `test_moved_calendar_pick_left_alone`: the reschedule path on a `calendar_pick` draft → not released, entry unchanged.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_ended_events.py -q`. Expected: the first two FAIL; the last two pass (guards).
- [ ] **Step 3: Implement**, and update the module docstring to say calendar-pick drafts follow their event too.
- [ ] **Step 4: Run** the Step 2 command (expected: PASS), then `make test` (expected: green).
- [ ] **Step 5: Commit** `fix(events): untouched calendar-pick drafts follow a cancelled event`.

### Task 7: One mapper for limit refusals; copy fixes (18.1-a, e, f)

**Files:**
- Modify:
  - `apps/web/src/lib/plan.ts`, `apps/web/src/lib/api.ts` (types `OwnerPlan`/`MyPlan` gain `backfill: boolean` and `scan_past_days_max: number | null`);
  - `apps/web/src/app/entries/[entryId]/page.tsx`, `apps/web/src/app/diaries/[diaryId]/page.tsx`, `apps/web/src/app/diaries/page.tsx`;
  - `apps/web/src/app/diaries/restore/page.tsx`, `apps/web/src/app/diaries/[diaryId]/restore/page.tsx`, `apps/web/src/app/diaries/new/page.tsx`;
  - `apps/web/src/components/SuggestionBox.tsx`, `EntryPlace.tsx`, `AddPhotos.tsx`, `GooglePhotosPick.tsx`;
  - `apps/web/src/app/diaries/[diaryId]/rules/page.tsx`;
  - every other place that shows an `ApiError` from an action that can be refused (find them with `grep -rn "ApiError\|\.message" apps/web/src`).
- Test: `apps/web/src/lib/__tests__/plan.test.ts`, plus the page and component tests listed below.

**Interfaces:**
- Produces:
  - `export function limitMessage(err: unknown, plan?: { plan_name: string; resets_on: string } | null): string | null`.
  - `export const BACKFILL_LIMIT_NOTE`.
  - `diaryLimitNote(planName: string | null, n: number)` and `readOnlyBanner(planName: string | null)`. `null` gives the "Your plan" copy from Global Constraints.

- [ ] **Step 1: Write the failing tests** in `plan.test.ts`:
  - `limitMessage(new Error('x'))` → `null`;
  - a non-`tier_limit` `ApiError` → `null`;
  - each source: `ai_draft` with `details.resets_on="2026-12-01"` → `"No AI drafts left this month. Resets Dec 1."`; `ai_draft` with no date anywhere → `"No AI drafts left this month."`; `photo` → `PHOTO_LIMIT_NOTE`; `diary` with limit 1 and no plan → `"Your plan: 1 diary."`; `diary_read_only` with plan Plus → `readOnlyBanner('Plus')`; `backfill` → `BACKFILL_LIMIT_NOTE`; `"something_new"` → `"Your plan doesn't include this."`;
  - `readOnlyBanner(null)` → the exact "your plan" copy;
  - `limitColor(0, 0) === 'red'` (backlog test, cheap).

  Page and component tests:
  - entries page: a regenerate rejected with `diary_read_only` shows the read-only copy, not the AI note;
  - SuggestionBox accept and EntryPlace save: a rejected `tier_limit` shows mapped copy, and `screen.queryByText(/tier_limit/)` is null;
  - restore page: a 403 `diary` with a failed plan lookup → `"Your plan: 1 diary."`;
  - diary and entry pages: the AI detail text contains `" · resets "` and not `", resets"`.
- [ ] **Step 2: Run** `cd apps/web && npx jest src/lib/__tests__/plan.test.ts src/app src/components`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** At each call site, try `limitMessage(err, plan)` first and fall back to the site's current handling.
- [ ] **Step 4: Run** `cd apps/web && npx jest` (expected: all pass) and `make typecheck lint` (expected: clean).
- [ ] **Step 5: Commit** `fix(web): plain copy for every plan refusal; one reset format; restore plan name`.

### Task 8: Gray out what the API refuses (18.1-b, 18.1-i web)

**Files:**
- Modify:
  - `apps/web/src/components/SuggestionBox.tsx`, `EntryPlace.tsx`;
  - `apps/web/src/app/entries/[entryId]/page.tsx` (photo detach);
  - `apps/web/src/app/diaries/[diaryId]/page.tsx` (Connect Google Calendar, Backfill, Scan ⚙ Past days);
  - `apps/web/src/app/diaries/[diaryId]/rules/page.tsx` (Apply days; load `diary.plan` if the page doesn't have it);
  - `apps/web/src/app/plans/page.tsx` (a Backfill row);
  - `COMPETITION.md` (a Backfill row in the plan table).
- Test: the existing `__tests__` for those pages and components, `apps/web/src/app/plans/__tests__/page.test.tsx`.

**Interfaces:**
- Consumes: `plan.read_only`, `plan.backfill`, `plan.scan_past_days_max` (Tasks 1 and 7), `BACKFILL_LIMIT_NOTE`, `LimitNote`, and the existing Upgrade now → `/plans` pattern.

- [ ] **Step 1: Write the failing tests:**
  - with `plan.read_only` true: SuggestionBox accept, the EntryPlace edit button, photo detach and Connect Google Calendar are `disabled`;
  - Free (`backfill: false`): the Backfill button is `disabled`, and `BACKFILL_LIMIT_NOTE` plus an "Upgrade now" link to `/plans` are shown;
  - the Past days input has `max="90"`, a typed 365 is sent as 90, and the note is shown;
  - Plus: Backfill is enabled, and Past days `max="3650"`;
  - rules page on Free: the Apply days input has `max="90"`, and a typed 120 is sent as 90;
  - plans page: a "Backfill" row (Free —, Plus ✓, Family ✓).
- [ ] **Step 2: Run** `cd apps/web && npx jest src/app src/components`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, following the existing read-only disabled pattern. Add the COMPETITION.md row next to the plan-table rows from item 18.
- [ ] **Step 4: Run** `cd apps/web && npx jest` (expected: all pass) and `make typecheck lint` (expected: clean).
- [ ] **Step 5: Commit** `feat(web): gray out read-only and paid-only controls; Backfill on the plans page`.

### Task 9: End-to-end checks and docs

**Files:**
- Modify:
  - `apps/web/e2e/tier-limits.spec.ts`;
  - `design/tier-followups-2026-11.md` (mark each 18.1 row done with its commit);
  - `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md` ("Decisions after the build" points to the 18.1 spec);
  - `POC_PHASE2_TODO.md` (row 18.1 → built, with the deploy note `make migrate (0018)`).

- [ ] **Step 1: Write the e2e tests**, using the file's existing helpers for setting tiers and seeding:
  - Free diary page: Backfill disabled with the note, and the Past days max is 90;
  - read-only diary: the Connect, SuggestionBox and EntryPlace controls are disabled;
  - a forced 403 (`page.route` returning a `tier_limit` body with `source: "photo"` on photo attach) shows `"Free plan: 1 photo per entry."`, and the page has no text matching `/tier_limit/`.
- [ ] **Step 2: Run** `make test-e2e`. Expected: the new tests pass on the built stack. If one fails, fix the code, not the test, and re-run the owning task's Jest tests.
- [ ] **Step 3: Update the docs** listed above.
- [ ] **Step 4: Run** `make test-all`. Expected: "All checks passed." Record the pytest and Playwright counts; the baseline is 1020 and 67.
- [ ] **Step 5: Commit** `test(e2e): tier follow-ups; docs: 18.1 built`.

## Verification (whole branch)

1. `make test-all` is green locally, with counts recorded in the PR body.
2. A manual check on the dev stack (`make up`, `make migrate`):
   - `./scripts/admin.sh users set-tier --email <test user> --tier free`. Backfill is grayed with the note. A Scan with Past days 365 can't be sent (capped at 90). `curl` with `past_days=365` gets 403 `source: "backfill"`.
   - Set the tier to `plus`. Backfill starts, and the Past days 365 scan is accepted.
3. Worker log check: cancel one event of a two-event rule entry in Google Calendar, or call `_cancel_calendar_event` in a shell. Expect one `generate_entry_draft` about 10 minutes later and `redraft_free` in the worker log.
