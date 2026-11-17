# AI-Working Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every entry whose AI draft is waiting or running shows "Queued" or "AI is writing…" in the diary list and on the entry page. It clears by itself when the draft finishes.

**Architecture:**
- **Data:** a new table, `ai_draft_jobs`, holds one row per queued draft task.
- **Queueing:** every draft is queued through one helper. The helper inserts the row and passes its id to the task as `job_id`. The task stamps `started_at` when it begins, and deletes the row in a `finally`. The existing 10-minute beat sweep deletes stale rows.
- **API:** the API exposes `ai_status` on entries, plus a small per-diary status endpoint.
- **Web:** the pages poll every 3 s while anything is pending.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, Postgres; Next.js/React with Jest and Testing Library.

**Spec:** `docs/superpowers/specs/2026-11-17-ai-working-indicator-design.md`

## Global Constraints

- Two states, exact strings:
  - List badge: `Queued` and `AI is writing…` (a single `…` character).
  - Entry-page notices:
    - Writing: `AI is writing this entry. Wait a moment for the draft, or edit anyway.`
    - Queued: `AI will write this entry shortly. Wait for the draft, or edit anyway.`
- API value: `ai_status: "queued" | "writing" | null`. An entry is "writing" if any of its jobs has `started_at`; else "queued" if any job exists; else null.
- **Best-effort indicator:** a failure to insert, mark or delete a job row is logged and never stops or fails a draft.
- **`updated_at` stays untouched:** nothing in this work writes to `entries`.
- **Stale cutoff:** a job is stale when `coalesce(started_at, run_after) < now() - LOST_DRAFT_AFTER` (30 min, `app/services/ai_drafts.py`).
- **Coalesced redraft window:** `REDRAFT_WINDOW_SECONDS` (600). A coalesced job's `run_after` is `queued_at + 600 s`.
- **Poll interval:** 3000 ms. Polling stops when nothing is pending, and after `MAX_POLL_FAILURES` consecutive errors (the constant the diary page already uses).
- **Viewers see only published entries** (as `list_entries` does today). The status endpoint applies the same filter.
- **Never weaken an existing test.** When a test breaks because a call now carries `job_id`, change only the expected arguments or the patch target. `assert_not_called` lines stay as they are.
- **`ruff`:** run it only on files you changed, never `ruff format` on the whole tree.
- **Commits:** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage explicit paths only; never `git add -A`.

## Review Focus

1. **A draft that raises an exception still deletes its job row.** Otherwise the spinner never stops. Test owned by Task 2.
2. **Two overlapping jobs on one entry.** Example: a coalesced redraft waiting while Regenerate runs. The entry shows writing, then queued, then none. Tests owned by Tasks 1 and 3.
3. **Viewers never learn about drafts** through the status endpoint, and soft-deleted entries never appear in it. Test owned by Task 3.
4. **The Redis-down fallback in `queue_redraft`** still creates a job row and passes `job_id`. Test owned by Task 2.
5. **Entries beyond the first page (after "load more") update** when their draft finishes, and a failing status endpoint stops the polling instead of retrying forever. Tests owned by Task 4.

---

### Task 1: The `ai_draft_jobs` table and its service

**Files:**
- Modify: `apps/api/app/models/__init__.py` (add `AiDraftJob` after `AiDraftSpend`, ~line 473)
- Create: `apps/api/alembic/versions/0019_ai_draft_jobs.py` (revision `"0019"`, down_revision `"0018"`)
- Create: `apps/api/app/services/ai_draft_jobs.py`
- Test: `apps/api/tests/integration/test_ai_draft_jobs.py`, `apps/api/tests/integration/test_migration_0019.py`

**Interfaces:**
- Produces: the model and five service functions.
  - `AiDraftJob`, with columns `id: UUID pk default uuid4`, `entry_id: UUID FK entries.id ON DELETE CASCADE, not null, indexed`, `queued_at: timestamptz server_default now() not null`, `run_after: timestamptz not null`, `started_at: timestamptz null`.
  - `async def add_job(db, entry_id: uuid.UUID, *, delay_seconds: int = 0, job_id: uuid.UUID | None = None) -> uuid.UUID`: inserts and flushes; the caller commits.
  - `async def mark_started(db, job_id: uuid.UUID) -> None`: sets `started_at = now()`; no error if the row is missing.
  - `async def finish_job(db, job_id: uuid.UUID) -> None`: deletes; no error if the row is missing.
  - `async def sweep_stale_jobs(db, now: datetime | None = None) -> int`: deletes stale rows (Global Constraints) and returns the count.
  - `async def ai_statuses(db, entry_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Literal["queued", "writing"]]`: one grouped query; entries with no job are absent; an empty input returns `{}` without querying.
  - `async def diary_ai_statuses(db, diary_id: uuid.UUID, *, published_only: bool) -> dict[uuid.UUID, Literal["queued", "writing"]]`: joins `entries`, excludes `deleted_at IS NOT NULL`, and with `published_only` keeps `status == "published"`.

- [ ] **Step 1: Write the failing tests** in `test_ai_draft_jobs.py`, using `make_user` / `make_diary` / `make_entry` from `tests/fixtures/factories.py` and the `db` session fixture the other integration tests use:
  - `test_add_job_reports_queued`: add one job; `ai_statuses(db, [e.id]) == {e.id: "queued"}`.
  - `test_started_job_reports_writing`: add, `mark_started`; the result is `"writing"`.
  - `test_overlapping_jobs_writing_then_queued_then_none`: two jobs on one entry, one started → `"writing"`; finish the started one → `"queued"`; finish the other → `{}`.
  - `test_finish_and_mark_missing_job_are_noops`: `mark_started(db, uuid4())` and `finish_job(db, uuid4())` don't raise.
  - `test_coalesced_job_run_after`: `add_job(..., delay_seconds=600)`; `run_after - queued_at == timedelta(seconds=600)` (within 1 s).
  - `test_sweep_deletes_stale_keeps_fresh`: with `now = datetime.now(UTC) + timedelta(minutes=31)`, a job queued now with `delay_seconds=0` is swept. A job with `delay_seconds=600` is kept (its `run_after` is 10 min later, so not stale at +31 min). A job started at "now" is swept at +31 min. The return value equals the number deleted.
  - `test_diary_statuses_filters`: entries in diary A (draft with a job, published with a job, soft-deleted with a job) and a job in diary B. `diary_ai_statuses(A, published_only=False)` returns the draft and the published entry only. `published_only=True` returns the published entry only. Diary B's entry never appears.
  - `test_job_deleted_with_entry`: hard-delete the entry row; the job row is gone (cascade).
- [ ] **Step 2: Run them; they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_draft_jobs.py -q`
  - Expected: FAIL at import (`AiDraftJob` / `app.services.ai_draft_jobs` missing).
- [ ] **Step 3: Implement the model, migration 0019 and the service.**
  - Migration: create the table plus index `ix_ai_draft_jobs_entry_id`; the downgrade drops both.
  - `ai_statuses`: `select(AiDraftJob.entry_id, func.bool_or(AiDraftJob.started_at.is_not(None))).where(entry_id.in_(ids)).group_by(entry_id)`.
- [ ] **Step 4: Write `test_migration_0019.py`** using the scratch-database pattern of `test_migration_0018.py`. Upgrade to 0019: the table and the index exist. Downgrade to 0018: the table is gone.
- [ ] **Step 5: Run the tests.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_draft_jobs.py tests/integration/test_migration_0019.py -q`
  - Expected: all pass.
- [ ] **Step 6: Commit.**
  - Stage the five files.
  - Message: `feat(api): ai_draft_jobs table and service (row 38)`.

---

### Task 2: Queue every draft through one helper; the task marks and clears its job

**Files:**
- Modify: `apps/api/app/workers/tasks.py`:
  - `generate_entry_draft` ~line 723 and `_generate_entry_draft` ~line 783.
  - `queue_redraft` ~line 748.
  - A new `enqueue_entry_draft`, placed above `queue_redraft`.
- Modify: `apps/api/app/workers/rules.py:174-181` (and the import at ~line 102).
- Modify: `apps/api/app/routers/v1/calendar_events.py:19,151-157`.
- Modify: `apps/api/app/routers/v1/entries.py:716-719`.
- Modify: `apps/api/app/workers/beat_tasks.py` `_release_lost_ai_drafts` ~line 123.
- Test:
  - `apps/api/tests/integration/test_ai_draft_jobs_queue.py` (new).
  - `apps/api/tests/unit/test_draft_queue_guard.py` (new).
  - Existing tests that break only because of the new `job_id` argument or the changed patch target (see Step 6).

**Interfaces:**
- Consumes: Task 1's `add_job`, `mark_started`, `finish_job`, `sweep_stale_jobs`.
- Produces:
  - `async def enqueue_entry_draft(entry_id: uuid.UUID | str, *, trigger: Literal["background", "regenerate"] = "background", coalesced: bool = False) -> None` in `app/workers/tasks.py`.
    1. Inserts a job and commits it via `app.workers.utils.db_session()`. `delay_seconds` is `REDRAFT_WINDOW_SECONDS` when coalesced, else 0. On an exception: log `ai_draft_job_add_failed` and continue with `job_id = None`.
    2. Sends:
       - Coalesced: `generate_entry_draft.apply_async((str(entry_id),), {"coalesced": True, "job_id": str(job_id)}, countdown=REDRAFT_WINDOW_SECONDS)`.
       - Regenerate: `generate_entry_draft.delay(str(entry_id), trigger="regenerate", job_id=str(job_id))`.
       - Otherwise: `generate_entry_draft.delay(str(entry_id), job_id=str(job_id))`.
       - When the insert failed, the `job_id` passed is `None`.
    3. If sending raises, it tries `finish_job` (best-effort), then re-raises.
  - `generate_entry_draft(self, entry_id, trigger="background", coalesced=False, job_id: str | None = None)` passes `job_id` through to `_generate_entry_draft(..., job_id=...)`.

- [ ] **Step 1: Write the failing tests** in `test_ai_draft_jobs_queue.py`. Patch `app.workers.tasks.generate_entry_draft` and use a real entry from the factories.
  - **Queueing tests:**
    - `test_enqueue_inserts_job_and_passes_job_id`: after `await enqueue_entry_draft(e.id)`, the entry's status is `"queued"`, and `task.delay` was called once with `(str(e.id),)` and `job_id=<that row's id as str>`.
    - `test_enqueue_regenerate`: the call is `delay(str(e.id), trigger="regenerate", job_id=ANY)`.
    - `test_enqueue_coalesced_uses_countdown`: the call is `apply_async((str(e.id),), {"coalesced": True, "job_id": ANY}, countdown=600)`, and `task.delay` is not called.
    - `test_send_failure_removes_job_and_reraises`: `task.delay.side_effect = RuntimeError`. `pytest.raises(RuntimeError)`, and the entry has no job afterwards.
    - `test_job_insert_failure_still_queues`: patch `app.services.ai_draft_jobs.add_job` to raise. `delay` is still called with `job_id=None`.
  - **Running tests:** `_generate_entry_draft` with `update_place_and_weather` and `generate_draft_for_entry` patched as `AsyncMock`.
    - `test_task_marks_started_then_clears`: inside the patched `generate_draft_for_entry` side effect, assert the entry's status is `"writing"`. After it returns, the entry has no job.
    - `test_task_clears_job_when_draft_raises`: `generate_draft_for_entry` raises `RuntimeError`. The error propagates, and the entry has no job. (Review Focus 1)
    - `test_task_without_job_id_writes_nothing`: `_generate_entry_draft(str(e.id))` with an existing unrelated job on the entry. That job is untouched.
  - **Redis-down fallback:** `test_queue_redraft_without_redis_creates_job` patches `app.core.dependencies.get_redis` to raise. `queue_redraft(e.id)` calls `delay(str(e.id), job_id=ANY)` and the entry is `"queued"`. (Review Focus 4)
  - **Beat sweep:** `test_beat_sweep_clears_stale_jobs` inserts a job with `queued_at` and `run_after` 31 minutes ago, then calls `beat_tasks._release_lost_ai_drafts()`. The job is gone.
- [ ] **Step 2: Write the guard test** in `tests/unit/test_draft_queue_guard.py`, as `test_drafts_are_queued_only_through_enqueue_entry_draft`.
  - Parse every `.py` under `apps/api/app` with `ast`.
  - Collect every call whose function is an attribute `delay` or `apply_async` on a name `generate_entry_draft`, together with the enclosing function's name.
  - Assert every such call is in `app/workers/tasks.py` inside `enqueue_entry_draft`, and that at least one exists.
- [ ] **Step 3: Run the new tests; they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_draft_jobs_queue.py tests/unit/test_draft_queue_guard.py -q`
  - Expected: FAIL. `enqueue_entry_draft` is missing, and the guard finds direct calls in `rules.py`, `calendar_events.py`, `entries.py` and `queue_redraft`.
- [ ] **Step 4: Implement.**
  - **The helper:** add `enqueue_entry_draft`.
  - **`queue_redraft`:** keep the Redis key logic. Its `.delay` fallback becomes `await enqueue_entry_draft(entry_id)`. The coalesced `apply_async` becomes `await enqueue_entry_draft(entry_id, coalesced=True)`, inside the existing `try`, so a send failure still releases the Redis key.
  - **The three other call sites:** each `generate_entry_draft.delay(...)` becomes `await enqueue_entry_draft(...)`, keeping its existing `try/except` logging. Drop the now-unused `generate_entry_draft` imports.
  - **`_generate_entry_draft`:**
    - When `job_id` is set, call `mark_started` before the weather step, each in its own `db_session()` and best-effort (log `ai_draft_job_mark_failed`).
    - Run the rest in `try`. Call `finish_job` in `finally`, best-effort (log `ai_draft_job_finish_failed`).
    - The coalesced Redis-key clear stays first.
  - **Beat sweep:** in `_release_lost_ai_drafts`, also call `sweep_stale_jobs(db)` and log its count. Keep returning the spend count.
- [ ] **Step 5: Run the new tests; they pass.** Same command as Step 3. Expected: all pass.
- [ ] **Step 6: Run the whole API suite and fix only the fallout.**
  - Run: `make test` (from the repo root)
  - Expected fallout:
    - Assertions like `.delay.assert_called_once_with(str(entry.id))` now need `job_id=ANY`, in `test_rules_evaluation.py`, `test_picker_endpoint.py`, `test_redraft_coalescing.py` and others.
    - Tests that patched `app.routers.v1.calendar_events.generate_entry_draft` or `app.workers.rules.generate_entry_draft` must patch `app.workers.tasks.generate_entry_draft` instead.
  - Apply the "never weaken" constraint. If a test fails for any other reason, stop and report it.
  - Expected after fixing: all pass.
- [ ] **Step 7: Lint the changed files.**
  - Run: `cd apps/api && .venv/bin/ruff check <changed files> && .venv/bin/ruff format --check <changed files>`
  - Expected: clean.
- [ ] **Step 8: Commit.**
  - Stage the changed files explicitly.
  - Message: `feat(worker): queue every draft through enqueue_entry_draft and track its job (row 38)`.

---

### Task 3: `ai_status` in the API

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py`:
  - `EntryOut` at line 150.
  - `list_entries` at ~399, `get_entry` at ~498, and the regenerate endpoint at ~700.
  - A new route.
- Test: `apps/api/tests/integration/test_ai_status_api.py` (new).

**Interfaces:**
- Consumes: Task 1's `ai_statuses`, `diary_ai_statuses`; Task 2's `enqueue_entry_draft`.
- Produces:
  - **`EntryOut.ai_status: Literal["queued", "writing"] | None = None`.**
    - Filled by `list_entries` (one `ai_statuses` call for the page's ids) and `get_entry`.
    - The regenerate endpoint returns `"queued"` (or `"writing"` if a job had already started), read after `enqueue_entry_draft`.
    - Every other response leaves it `None`.
  - **`GET /v1/diaries/{diary_id}/entries/ai-status`**, response model `AiStatusMapOut { entries: dict[str, Literal["queued", "writing"]] }`.
    - Uses `_get_diary_or_404`, so non-members get a 404.
    - `published_only = (role == "viewer")`.
    - Declare it before any route it could collide with.

- [ ] **Step 1: Write the failing tests** in `test_ai_status_api.py`, using the authenticated-client pattern from `test_entries.py`. Create jobs with Task 1's `add_job` and `mark_started`, then commit.
  - **Status on entries:**
    - `test_list_reports_each_entry_status`: three entries (queued, writing, none). The list returns `ai_status` `"queued"`, `"writing"` and `null` respectively.
    - `test_get_reports_writing_with_overlapping_jobs`: one started job and one queued job on the same entry. `GET /v1/entries/{id}` returns `"writing"`. (Review Focus 2)
    - `test_regenerate_returns_queued`: patch `app.workers.tasks.generate_entry_draft`. `POST /v1/entries/{id}/regenerate` returns `ai_status == "queued"`.
  - **The status endpoint:**
    - `test_status_endpoint_owner`: returns `{"entries": {id: status}}` for the diary's pending entries only, never another diary's.
    - `test_status_endpoint_viewer_sees_published_only`: a viewer permission on the diary; a draft and a published entry, both with jobs. The viewer gets only the published entry. (Review Focus 3)
    - `test_status_endpoint_excludes_trashed`: a soft-deleted entry with a job is absent.
    - `test_status_endpoint_non_member_404`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_status_api.py -q`
  - Expected: FAIL (no `ai_status` key; the route returns 404 or 422).
- [ ] **Step 3: Implement** as in the Interfaces block. `_entry_out_from_orm` stays unchanged: set `ai_status` on the returned `EntryOut` objects with `model_copy(update=...)` or by assignment.
- [ ] **Step 4: Run the tests.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_status_api.py tests/integration/test_entries.py tests/integration/test_regenerate_endpoint.py -q`
  - Expected: all pass.
- [ ] **Step 5: Lint the changed files** (same command as Task 2 Step 7), then commit.
  - Message: `feat(api): ai_status on entries and a per-diary status endpoint (row 38)`.

---

### Task 4: The badge and live updates on the diary page

**Files:**
- Modify: `apps/web/src/lib/api.ts`:
  - The `Entry` type, ~line 320.
  - The `entries` client, ~line 575.
- Create: `apps/web/src/components/AiStatusBadge.tsx`.
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`:
  - `EntryCard`, line 20.
  - Entry state and polling, near the existing `usePolling` calls at ~236 and ~273.
- Modify: `apps/web/src/app/globals.css` (only if a reduced-motion rule for `.spinner` is missing; the spinner itself is at line 257).
- Test:
  - `apps/web/src/components/__tests__/AiStatusBadge.test.tsx` (new).
  - `apps/web/src/app/diaries/[diaryId]/__tests__/page.test.tsx`.

**Interfaces:**
- Consumes: Task 3's `ai_status` field and its status endpoint.
- Produces:
  - **Types:** `export type AiStatus = 'queued' | 'writing'`, and `Entry.ai_status: AiStatus | null`.
  - **Client:** `api.entries.aiStatus(diaryId: string): Promise<Record<string, AiStatus>>` (GETs the endpoint and returns `.entries`).
  - **Component:** `export function AiStatusBadge({ status }: { status: AiStatus | null })`.
    - Renders nothing for `null`.
    - Otherwise renders `<span role="status" data-testid="ai-status">` with an icon and the exact text `Queued` or `AI is writing…`.
    - The writing icon reuses the `.spinner` class. The queued icon is a static clock (an inline SVG or the ⏱ character, the builder's choice) with `aria-hidden`.

- [ ] **Step 1: Write the failing tests.**
  - **Badge** (`AiStatusBadge.test.tsx`):
    - `renders nothing when idle`.
    - `shows Queued`: text `Queued`, no `.spinner`.
    - `shows AI is writing… with a spinner`.
  - **Diary page** (`page.test.tsx`). Extend the existing mocks of `api.entries.list`, and use Jest fake timers.
    - `shows the badge on pending entries`: an entry with `ai_status: 'queued'` shows `Queued` in its card.
    - `polls while pending and refetches a finished entry`.
      - Setup: `aiStatus` resolves to `{e1: 'writing'}`, then `{}`. `api.entries.get('e1')` resolves to the entry with a title and `ai_status: null`.
      - After advancing 3000 ms twice, the card shows the new title and no badge. `api.entries.get` was called with `'e1'` once.
    - `does not poll when nothing is pending`: `aiStatus` is never called.
    - `stops polling after repeated failures`: `aiStatus` rejects. After `MAX_POLL_FAILURES` intervals plus two more, its call count equals `MAX_POLL_FAILURES`. (Review Focus 5)
    - `updates an entry loaded by load more`: the second page holds the pending entry, and it still updates. (Review Focus 5)
- [ ] **Step 2: Run them; they fail.**
  - Run: `cd apps/web && npx jest src/components/__tests__/AiStatusBadge.test.tsx "src/app/diaries/\[diaryId\]/__tests__/page.test.tsx"`
  - Expected: FAIL (the module is missing; there's no badge text).
- [ ] **Step 3: Implement.**
  - **`EntryCard`:** render `<AiStatusBadge status={entry.ai_status ?? null} />` in the badge row, next to the status badge.
  - **The diary page:**
    - Derive `aiPending = entries.some(e => e.ai_status)`.
    - Run `usePolling(pollAiStatus, 3000, aiPending && aiFailures < MAX_POLL_FAILURES)`.
    - `pollAiStatus` fetches the map. Entries still in it get their `ai_status` updated in place. Each entry that had a status and is now absent is re-fetched with `api.entries.get` and replaced in place, so its position and pagination stay put.
    - A failure increments a counter; a success resets it.
  - **Reduced motion:** add `@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }` if it isn't there.
- [ ] **Step 4: Run the tests.**
  - Run: same as Step 2, then `cd apps/web && npx jest && npx tsc --noEmit && npx next lint`
  - Expected: all pass, and the type check and lint are clean.
- [ ] **Step 5: Commit.**
  - Message: `feat(web): AI status badge and live updates on the diary page (row 38)`.

---

### Task 5: The notice and live updates on the entry page; tracker

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`:
  - The notice goes above the body, near line 484.
  - Polling goes near `pollRegen`, at ~206.
- Test: `apps/web/src/app/entries/[entryId]/__tests__/page.test.tsx`.
- Modify: `POC_PHASE2_TODO.md` row 38: status `**done** 2026-11-17`, with a one-line summary and the spec path.

**Interfaces:**
- Consumes: Task 4's `AiStatus` and `Entry.ai_status`; `api.entries.get`.

- [ ] **Step 1: Write the failing tests** in `page.test.tsx`, extending its existing mocks.
  - **The notice:**
    - `shows the writing notice`: an entry with `ai_status: 'writing'` shows the exact writing notice from Global Constraints.
    - `shows the queued notice`: same, with the exact queued notice.
    - `hides the notice while Regenerate runs`: click Regenerate on an entry whose response carries `ai_status: 'queued'`. The Regenerate status panel shows, and neither notice is present.
  - **Polling:**
    - `polls and shows the draft when AI finishes`, with fake timers. `api.entries.get` first returns `ai_status: 'writing'`, then the entry with a body and `ai_status: null`. After 3000 ms the body text appears and the notice is gone.
    - `keeps an edit in progress when AI finishes`. Click Edit and type into the body field. When the poll returns the finished entry with a `suggestion`, the typed text is still in the field and `ai_status` has cleared. (In this case, merge only `ai_status`, `suggestion` and `last_generation` into the entry.)
    - `edit stays enabled while AI works`: the Edit button is enabled when `ai_status` is `'writing'`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `cd apps/web && npx jest "src/app/entries/\[entryId\]/__tests__/page.test.tsx"`
  - Expected: FAIL (no notice text).
- [ ] **Step 3: Implement.**
  - **The notice:** shown while `entry.ai_status && !pollingRegen`.
  - **Polling:** `usePolling(pollAi, 3000, !!entry?.ai_status && !pollingRegen && failures < MAX_POLL_FAILURES)`. Define a local `MAX_POLL_FAILURES = 5` if the page has none, matching the diary page's value.
  - **Merging:** when editing, merge `ai_status`, `suggestion` and `last_generation` only; otherwise replace the entry.
- [ ] **Step 4: Run the tests.**
  - Run: same as Step 2, then `cd apps/web && npx jest && npx tsc --noEmit && npx next lint`
  - Expected: all pass, and the type check and lint are clean.
- [ ] **Step 5: Update tracker row 38 and commit both.**
  - Message: `feat(web): AI-working notice and live updates on the entry page; row 38 done`.

---

## After all tasks (controller)

1. **Run `make test-all`** (lint, typecheck, API, e2e). Expected: `All checks passed.`
2. **Then run the dev-stack check:** `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`. Expected: smoke passes and migration 0019 applies.
3. **Check by hand on localhost (spec § Verification):**
   - A rule matching several past events, then a scan: the new entries show Queued or AI is writing… and fill in without a refresh.
   - Open one mid-draft: the notice shows.
   - Type something: the AI text arrives as a suggestion.
4. **Open the PR.** Andrew merges, or I merge when CI is green under his standing OK. Then deploy with `sudo ./scripts/nuc/40-update.sh`, where 0019 migrates, and Andrew checks on his phone.
