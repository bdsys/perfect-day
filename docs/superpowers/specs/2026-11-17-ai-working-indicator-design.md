# Show that AI is working on a draft — design (tracker row 38)

Approved approach: Andrew, 2026-11-17 ("yes go with A").

## Why

From Andrew's phone checks (2026-11-16): when a rule runs, a list of empty
entries appears and nothing says the AI is about to fill them. "It's easy to
go in and start editing without realizing that they just need to wait."

Nothing is lost when that happens: the AI never overwrites a person's text,
so its draft lands as a suggestion beside what they typed
(`workers/llm.py`, follow-up 25.3). The cost is wasted effort and confusion,
not data.

## Goals

- Every entry whose AI draft or redraft is waiting or running says so, in the
  diary's entry list and on the entry page. It still says so after a page
  reload, on another device, or for a second person viewing the diary.
- Two states (Andrew's choice):
  - **Queued**: a quiet clock icon. The draft is waiting, either for a
    worker slot (the NUC has 2) or for the deliberate 10-minute coalescing
    window of an automatic redraft (`REDRAFT_WINDOW_SECONDS`).
  - **AI is writing…**: a spinner. A worker has picked the draft up.
- The indicator clears by itself when the draft finishes, whatever the outcome:
  success, the deterministic fallback, failure, or the plan limit. A page
  that is open shows the new text without a manual refresh.
- A worker that dies mid-draft never leaves a spinner turning forever.

## Non-goals

- Locking the entry. Edit stays enabled while AI works (my call, see below).
- Progress percentages or time estimates.
- Push or email notification that a draft is ready (item 20).
- Changing how drafts are queued, coalesced, reserved or retried.
- Indicators for photo imports, scans or backfills (they have their own panels).

## Anti-goals

- A spinner that never stops. Any path that can leave a pending row must be
  covered by the finish-time delete or the sweep.
- Showing "writing" when nothing will happen, or nothing when something will.
- Making the drafting path fail because the indicator could not be written:
  the indicator is best-effort, the draft is not.
- Touching `entries.updated_at`: the current Regenerate poll and the
  "last edited" time rely on it.

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | A new table, one row per queued draft task, deleted when the task finishes | One migration and one extra query; correct when two drafts for one entry overlap (Regenerate clicked while a redraft waits); never touches the entry row | **Picked** |
| B | Two timestamp columns on `entries` | Simplest to read; whichever of two overlapping drafts finishes first clears the state; every write bumps `updated_at` | Rejected |
| C | Short-lived Redis keys | No migration and self-expiring; silently shows nothing when Redis is down; the list needs a Redis lookup per entry | Rejected |

## The design

### Data: `ai_draft_jobs` (migration 0019)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid, primary key | The job id. It's passed to the draft task as a `job_id` argument (see below) |
| `entry_id` | uuid, FK `entries.id` `ON DELETE CASCADE`, indexed | |
| `queued_at` | timestamptz, default now() | |
| `run_after` | timestamptz | `queued_at` plus the countdown (600 s for a coalesced redraft, else equal to `queued_at`) |
| `started_at` | timestamptz, null | Set when a worker picks the task up |

An entry's status: **writing** if any of its rows has `started_at`; else
**queued** if it has any row; else none.

### Queueing: one helper for every draft

`enqueue_entry_draft(entry_id, *, trigger="background", coalesced=False, countdown=None)`
in `app/workers/tasks.py`:

1. Make a job id (`uuid4`).
2. Insert the job row in its own short transaction and commit. If that fails,
   log it and still queue the task (best-effort indicator).
3. Queue the task with `job_id` as an extra keyword argument, the same way as
   today: `.delay(...)`, or `.apply_async(..., countdown=600)` for a coalesced
   redraft.
4. If sending fails, delete the row, then re-raise as the call sites expect today.

Planning note (2026-11-17): the job id is a separate argument rather than a
Celery task id fixed in advance. Fixing the task id would force every queue
call onto `apply_async`. About 40 existing tests assert
`generate_entry_draft.delay.assert_not_called()`, and those would then pass
without testing anything.

Every place that queues `generate_entry_draft` goes through it. Today that's:

- `routers/v1/calendar_events.py:152`, a draft from "New entry from Google Calendar".
- `routers/v1/entries.py:719`, Regenerate (`trigger="regenerate"`).
- `workers/rules.py:179`, a new entry from a rule.
- `workers/tasks.py` `queue_redraft`, both the coalesced `apply_async` and the no-Redis `.delay` fallback.

A new test must fail if any other module calls `generate_entry_draft.delay` or
`.apply_async` directly, so a future call site can't skip the indicator.

The API routes use the request's database session for the insert, not a
separate one. The insert has to commit before the task is sent, because a
worker that starts first must find the row. The builder settles the exact
transaction handling at each call site. Rule: the row is committed before
the send.

### Running: mark started, always clean up

At the top of the draft, set `started_at = now()` on the row whose `id` is
the task's `job_id`. With no `job_id` or no row, nothing is written: that's a
task queued before this deploy. Wrap the whole draft in `try/finally` and
delete that row in the `finally`. That covers success, fallback, failure, the
plan limit, an early return and an exception. Both writes are best-effort:
log on error, never fail the draft.

A redelivered task (worker killed, Celery redelivers) finds its row still
there, sets `started_at` again and deletes it at the end. The behaviour
stays correct.

### Sweep

The existing beat task `release_lost_ai_drafts` runs every 10 minutes. It
also deletes jobs where `coalesce(started_at, run_after) < now() - 30 min`,
reusing `LOST_DRAFT_AFTER`. Worst case, a dead worker's spinner lingers
about 40 minutes.

### API

- `EntryOut` gains `ai_status: "queued" | "writing" | null`. It's filled for
  `GET /v1/entries/{id}` and `GET /v1/diaries/{id}/entries` with one grouped
  query per request, not one per entry. The trash list returns `null`.
- New `GET /v1/diaries/{diary_id}/entries/ai-status` returns
  `{"entries": {"<entry_id>": "queued" | "writing"}}` for that diary's
  non-deleted entries that have a job. It uses the same view permission as
  the entry list. It exists so polling stays cheap.
- Viewers see the status too. It reveals nothing they can't already see.

### Web

- **`EntryCard`** (diary page) shows the state next to the title.
  - Queued: a clock icon and "Queued".
  - Writing: a spinner and "AI is writing…".
  - The text is real text, not only an icon. The spinner respects
    `prefers-reduced-motion`.
- **Entry page** shows a notice above the body while `ai_status` is set:
  - Writing: "AI is writing this entry. Wait a moment, or edit anyway: your text is kept and the AI draft appears as a suggestion."
  - Queued: "AI will write this entry shortly. You can wait, or edit anyway: your text is kept and the AI draft appears as a suggestion."
  - **Only one indicator at a time:** while the existing Regenerate panel (`StatusPanel`) is running, the notice is hidden.
- **Polling** uses the existing `usePolling`.
  - **Diary page:** while any loaded entry has a status, poll the ai-status endpoint every 3 s. When an entry leaves the map, refetch the entry list once so its new title and text appear.
  - **Entry page:** while its entry has a status, poll `GET /v1/entries/{id}` every 3 s, and update the entry when the status clears. Don't overwrite an edit in progress.
  - **Stop:** polling stops when nothing is pending, and after the existing `MAX_POLL_FAILURES`-style limit on errors.

## My calls (Andrew can override)

- **Edit stays enabled.** A notice explains the wait. Nothing is lost, so a lock would only get in the way.
- **"Queued" carries no timer or ETA.**
- **Notice wording** is as given above.
- **3-second poll.** Same order as the scan poll (2 s) and backfill poll (3 s).
- **A separate small status endpoint** for polling, not the full list.

## Left to the builder

- The exact component and markup for the badge and the notice. Match the
  existing inline-style conventions.
- The transaction mechanics at each call site, within the rule above.
- The migration's index names.

## Verification

- **API tests:**
  - The helper inserts a row and removes it when the send fails.
  - The task sets `started_at` and deletes the row on success, failure, plan
    limit and exception.
  - The sweep deletes stale rows and keeps fresh ones, including a coalesced
    row whose `run_after` is in the future.
  - `ai_status` is correct in the list and the detail, including two
    overlapping jobs where one has started.
  - The ai-status endpoint is limited to its diary and enforces permissions.
  - The guard test fails on a direct `.delay`.
- **Web tests:**
  - `EntryCard` shows both states.
  - The entry page shows the notice, and hides it while Regenerate runs.
  - Polling starts and stops, and the list is refetched when an entry clears.
- **Suites:** `make test-all`, then the dev-stack check from `CLAUDE.md`.
- **By hand on localhost:** create a rule that matches several past events
  and run a scan. The new entries show Queued or AI is writing… and fill in
  without a refresh. Open one mid-draft: the notice shows. Type, and the AI
  text arrives as a suggestion.
- **On the NUC after deploy:** the same check from Andrew's phone.
