# Item 18.1 — tier enforcement follow-ups: design

Status: approved 2026-11-15; built and merged in PR #54 (2026-11-16). Changes made during the build (rulings P1–P5, R6-1, R8-1, F1) are in #54's description; later decisions are in `design/tier-followups-2026-11.md`.
Parent spec: `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md`. This spec changes it where the two differ.
Source list: `design/tier-followups-2026-11.md`. Original review findings: <https://github.com/bdsys/perfect-day/pull/53>.

## Why

Item 18 put plan limits in place, and its review left nine things a parent can see:
- raw error words on screen;
- buttons that look usable but are refused;
- a spinner that never stops;
- a lost AI draft after a worker crash;
- two reset-date formats;
- a wrong plan name;
- AI text about a cancelled event.

Andrew also made three product decisions after the build. Free parents shouldn't pay for the calendar changing under them. Backfill is a reason to upgrade. Inviting stays open. This item closes all of that, so Free works the way a busy parent expects: nothing broken-looking, nothing that silently costs them.

## Decisions (Andrew, 2026-11-15)

| # | Decision | Detail |
|---|---|---|
| D1 | **Automatic redrafts are free.** | Free means an automatic (`trigger="background"`) draft of an entry that already has an AI draft. The redrafts that exist today are:<ul><li>an event cancelled (`tasks.py:470`);</li><li>an event moved to a time that hasn't passed (`tasks.py:412`);</li><li>a new event joining an existing spanning entry (`rules.py:278`).</li></ul>The first automatic draft of a new entry still counts. Generate, Polish and Regenerate still count. Redrafting when an event's title, location or description is *edited* doesn't exist today. It is backlog row 33, not part of 18.1. |
| D2 | **Free redrafts are coalesced.** | At most one free redraft per entry per 10 minutes. Changes that arrive meanwhile are folded into it. There is no monthly cap. |
| D3 | **Backfill is paid; Free looks back 90 days.** | Plus/Family only: `POST /v1/diaries/{id}/scan/backfill`. Free is capped at 90 days, the same as the free scan, for:<ul><li>Scan (`POST /v1/diaries/{id}/scan/run`) with `past_days` over 90;</li><li>rule "apply to past events" (`POST /v1/rules/{id}/apply`) with `days` over 90.</li></ul>Apply only re-checks events already stored (`tasks.py:661-668`), so a Free parent's new rule can still match the last 90 days. `future_days` is not limited. |
| D4 | **Inviting stays allowed on read-only diaries.** | No change, and nothing to build. |
| D5 | **Untouched "New entry from Google Calendar" drafts follow their event.** | When the only event is cancelled, the draft goes to Trash, as rule drafts already do. |
| D6 | **One branch, one PR.** | `feat/18.1-tier-followups` is built on `docs/tier-followups` (ff6a10b). |

## Goals

- Every limit refusal on screen reads as plain copy. None shows the raw "tier_limit".
- Every control the API refuses on a read-only diary or on Free is grayed out first, with the reason.
- No backfill spinner waits forever.
- A killed worker never costs the parent a draft for more than about half an hour.
- Calendar churn never spends a Free parent's drafts, and can't run up an unbounded LLM bill.
- Free can't backfill by any route.

## Non-goals

- Anything in group 3 (NUC performance) or group 4 (backlog) of `design/tier-followups-2026-11.md`.
- Moved events on `calendar_pick` entries (D5 covers cancellation only).
- A public `GET /v1/plans` (Ruling R6 stands).
- Billing.

## Anti-goals

- A free redraft must never overwrite text a person wrote. The existing `write_in_place` and `parents_writing` rules are unchanged.
- No change may make a Plus or Family parent wait longer for a first draft than they do today.
- No new limit is enforced only in the browser. Each one has an API refusal with the R3 shape.

## Constraints, carried from item 18

- Wire shape (R3): 403 `{"detail": {"code": "tier_limit", "details": {limit, current, source, required_tier, resets_on?}}}`.
- Guard order (R2): access, then `ensure_diary_writable`, then the plan limit.
- Unknown tier = free. Plan math lives only in `services/tier.py`.
- Facts from source data only. Text a person wrote is never changed by a background job.
- TDD: every fix starts with a failing test.
- Before any push: `make test-all` locally.

## The fixes

### a. One mapper for limit refusals (18.1-a)

Add to `apps/web/src/lib/plan.ts`:

```ts
limitMessage(err: unknown, plan?: { plan_name: string; resets_on: string } | null): string | null
```

It returns `null` when `err` is not an `ApiError` with `code === 'tier_limit'`. Otherwise it maps `details.source` to copy:

| source | copy |
|---|---|
| `ai_draft` | `aiLimitNote(details.resets_on ?? plan.resets_on)`; if neither is known: "No AI drafts left this month." |
| `photo` | `PHOTO_LIMIT_NOTE` |
| `diary` | `diaryLimitNote(planName, details.limit)` |
| `diary_read_only` | `readOnlyBanner(planName)` |
| `backfill` | `BACKFILL_LIMIT_NOTE` (see i) |
| anything else | "Your plan doesn't include this." |

`planName` is `plan?.plan_name`. When there's no plan, `diaryLimitNote` and `readOnlyBanner` take `null` and say "Your plan: 1 diary." and "This diary is read-only on your plan. Upgrade, or delete another diary, to use it again."

Every call site that can receive a 403 `tier_limit` uses `limitMessage` first, then falls back to its current error handling. Known sites:
- SuggestionBox accept;
- EntryPlace save;
- photo detach;
- AddPhotos / GooglePhotosPick;
- Generate/Polish/Regenerate on the entry page (today it assumes every `tier_limit` is the AI one, at `entries/[entryId]/page.tsx:202`);
- the diary page's Scan, Backfill and Connect;
- the rules page's Apply;
- diary create;
- both restore pages.

The builder greps for `ApiError` and `.message` displays to find any others.

### b. Gray out the remaining read-only controls (18.1-b)

When `plan.read_only` is true, these are disabled the same way the existing read-only controls are:
- SuggestionBox accept;
- EntryPlace edit;
- photo detach on the entry page;
- Connect Google Calendar on the diary page.

The read-only banner already explains why, so no new copy is needed.

### c. A backfill skipped as read-only finishes (18.1-c)

In `_backfill_diary` (`workers/tasks.py`, the `is_diary_read_only` skip near line 899), and only when the run is still `pending`, set it to `status="failed"`, `completed_at=now` and `error="This diary is read-only on your plan, so the backfill didn't run."` before returning. The skip runs before the `status != "pending"` guard (line 913), so without that check a run the parent already cancelled would be overwritten. Move the skip after the guard, or check the status inside it. The diary page already shows `error` for a failed run (`[diaryId]/page.tsx:653-655`).

The same function also gets the D3 plan check (see i).

### d. Give back drafts lost to a killed worker (18.1-d)

Add a beat task, `app.workers.beat_tasks.release_lost_ai_drafts`, that runs every 10 minutes (`workers/celery_app.py`).

The sweeper matches each spend to **its own** generation, not to "any later success on the entry". Matching per entry would charge twice in the very case this fix is for: a first draft is killed (spend S1), the parent retries (spend S2, success), and that later success would keep S1. So:

**Migration 0018:**
- `llm_generations.reservation_key` (nullable text, indexed).
- `ai_draft_spends.sweepable` (boolean, NOT NULL, `server_default false`).

Rows that exist before the migration stay `false`, so deploying never refunds a month of drafts already used.

**Writes:**
- `reserve_ai_draft` inserts new spends with `sweepable=True`.
- Every `llm_generations` row written by a draft that holds a reservation sets `reservation_key` to that key. These are the success rows at `workers/llm.py:860` and `:884`, the suggestion path among them.
- Free redrafts (h) hold no reservation and write `NULL`.

**The sweeper** deletes each `ai_draft_spends` row that meets all of these:
- `sweepable` is true;
- `created_at` is older than **30 minutes**;
- `entry_id` is not NULL;
- no `llm_generations` row exists with `reservation_key = spend.key` and `status='success'`.

Rows whose `entry_id` is NULL (entry hard-deleted) are kept: we can't tell whether they were used. The task logs one line with the count it released.

The builder check confirmed that every kept reservation writes its success row in the same transaction, and that every other outcome releases (`llm.py:946-948`, `519-524`).

`task_reject_on_worker_lost` stays at its default, as the item 18 reviewer advised.

### e. One reset-date format (18.1-e)

The detail text on `diaries/[diaryId]/page.tsx:379` and `entries/[entryId]/page.tsx:29` uses "· resets {date}", the same as the badge and the diary list.

### f. Restore page plan name (18.1-f)

`diaries/restore/page.tsx:48` passes `plan?.plan_name ?? null`, giving "Your plan: 1 diary.", instead of `'Free'`. Check the per-diary restore page too.

### g. Untouched calendar-pick drafts follow their event (18.1-g, D5)

In `workers/event_changes.py`, `release_event_from_entry` treats an untouched `calendar_pick` draft like an untouched rule draft:
- with no events left, it moves to Trash;
- with events left, its id is returned so a redraft is queued.

"Untouched" means:
- `status == 'draft'`;
- `deleted_at` is NULL;
- `creation_source` is `rule` or `calendar_pick`;
- `body_source` is `llm` or `fallback`, **or** `body_source` is NULL and the body is empty.

The NULL case is a new entry whose event was cancelled before its first draft was written. It would otherwise be left as an empty draft with no events.

The series-claim delete stays; it's a no-op for calendar picks.

The wider meaning gets a new predicate, `is_untouched_auto_draft`, used only by `release_event_from_entry`. `is_untouched_rule_draft`'s other caller, `_release_from_untouched_draft` (`tasks.py:483-492`, the reschedule path), stays rule-only. Widening it would release moved calendar-pick drafts, which is a non-goal.

The `no_inputs` branch in `workers/llm.py` is unchanged.

### h. Free, coalesced redrafts (18.1-h, D1, D2)

**Free at run time.** In `generate_draft_for_entry` (`workers/llm.py`), skip `reserve_ai_draft` when both of these hold:
- `trigger == "background"`;
- the entry has at least one `llm_generations` row with `status='success'`.

The at-limit branch is then never reached for that draft. The draft still writes its normal generation row, plus a log line `redraft_free`. It never spends, so there's nothing to release.

This is decided when the task runs, never when it's queued, because a pending first draft may succeed in between.

**Coalesced at queue time.** Add `queue_redraft(entry_id: uuid.UUID) -> None` in `workers/tasks.py`:
- `SET redraft_pending:{entry_id} 1 NX EX 900` in Redis.
- If it was set: `generate_entry_draft.apply_async((str(entry_id),), {"coalesced": True}, countdown=600)`.
- If the key already existed: log `redraft_coalesced` and do nothing.
- If Redis raises: fall back to `generate_entry_draft.delay(str(entry_id))`, today's behaviour.

`generate_entry_draft` gains `coalesced: bool = False`. Only a coalesced task deletes `redraft_pending:{entry_id}`, before it reads the entry, so a change after that point queues a fresh redraft. A Regenerate or first draft that runs meanwhile leaves the key alone, so D2's one-per-10-minutes still holds.

`queue_redraft` replaces the direct `.delay` at three places:
- `workers/tasks.py:412` (reschedule);
- `workers/tasks.py:470` (cancel);
- `workers/rules.py:171`, only for the spanning join at `rules.py:278`, the one case where an event joins an existing entry and a redraft is queued.

`_entry_for_event` returns a third flag saying so. The series-claim join (`:231`) queues nothing. New entries (`:239`, `:281`) and `calendar_events.py:152` keep the immediate `.delay`, so first drafts aren't delayed (anti-goal 2).

Coalescing applies on every plan, because it also stops "N grouped events = N regenerations" (the comment at `rules.py:276-277`).

### i. Backfill is paid (18.1-i, D3)

**`services/tier.py`:**
- `PlanLimits` gains `backfill: bool` and `scan_past_days: int | None`.
- Free is `False, 90`. Plus and Family are `True, None`.
- `LimitSource` gains `"backfill"`.
- `required_tier("backfill", …)` returns the first plan with `backfill=True`, which is `plus`.

**`ensure_backfill_allowed(owner, past_days: int | None)`** lives in `services/tier.py`:
- `past_days=None` means a date-range backfill. It's refused when `backfill=False`.
- A number is refused when `scan_past_days` is set and `past_days > scan_past_days`.

It raises `tier_limit_error(source="backfill", limit=90, current=past_days or 0, …)`.

**Guards**, after access and `ensure_diary_writable` (R2):
- `POST /v1/diaries/{id}/scan/backfill`: `ensure_backfill_allowed(owner, None)`.
- `POST /v1/rules/{id}/apply`: `ensure_backfill_allowed(owner, body.days)`.
- `POST /v1/diaries/{id}/scan/run`: `ensure_backfill_allowed(owner, body.past_days)`, only when `past_days` is given.

The plan that counts is the diary owner's, as for every other limit.

**Workers:** `_backfill_diary` and `_apply_rule_backfill` check the owner's plan when they start. A run queued before a downgrade then doesn't run:
- a pending backfill run ends `failed` with `error = BACKFILL_LIMIT_NOTE`, guarded like c;
- a rule apply over the cap logs `backfill_not_on_plan` and returns.

**Plan payload:** `OwnerPlanOut` and `MyPlanOut` gain `backfill: bool` and `scan_past_days_max: int | None`. Mirror them in `lib/api.ts`.

**Web:**
- Diary page: Backfill is disabled when `!plan.backfill`. Under it goes a `LimitNote` with `BACKFILL_LIMIT_NOTE` and **Upgrade now** → `/plans`, the same pattern as the AI note.
- Scan ⚙ "Past days": `max={plan.scan_past_days_max ?? 3650}`. It's clamped on submit, with the same note shown under the field when the cap applies.
- Rules page: "Apply to past events" caps its days field at `scan_past_days_max`, with the same note when the cap applies.
- `/plans` page: add a "Backfill" row (Free —, Plus ✓, Family ✓) and keep its static copy in step (R6).
- `COMPETITION.md` plan table: the same row.

**Copy:** `BACKFILL_LIMIT_NOTE = "Backfill is on Plus and Family. Free scans look back 90 days."`

## Docs to update in the same PR

- `design/tier-followups-2026-11.md`: mark D1–D5 answered, and mark each 18.1 row done.
- The parent spec's "Decisions after the build" section.
- `POC_PHASE2_TODO.md` row 18.1 → merged when it merges.
- The deploy note: `make migrate` (0018) before starting the new API and worker.
- `COMPETITION.md` (i).

## Left to the builder

- Test file layout, and fixtures that follow item 18's.
- Exact component wiring for disabled states.
- Whether `limitMessage` lives in `plan.ts` or a sibling file (`plan.ts` preferred).
- The name of the widened untouched-draft predicate.

## My calls (Claude), for Andrew to check

- **Sweeper age is 30 minutes**, not the reviewer's 15. A draft can legitimately take several minutes (3 attempts × 2 providers). If it's wrong, a parent waits up to about 40 minutes for a lost draft to come back.
- **Coalescing waits 10 minutes, then drafts (trailing).** It doesn't draft immediately and then hold. Background redrafts aren't watched live, and one call per burst is the cheapest. If it's wrong, a draft for a moved, cancelled or joined event updates about 10 minutes later than today.
- **The sweeper keys on the reservation, which needs migration 0018.** The cheaper per-entry match would charge twice when a parent retries after a crash. `sweepable` stops the deploy from refunding drafts already used. If it's wrong, it's one more `make migrate` on deploy.
- **An empty calendar-pick entry whose event is cancelled before its first draft is trashed too.** If it's wrong, the parent finds an empty entry in Trash instead of in the diary.
- **Only the delayed redraft clears its pending key.** A Regenerate never does. If it's wrong, at worst one redraft runs about 10 minutes after a Regenerate.
- **Coalescing applies to every plan**, not just Free, because it also fixes the grouped-events regeneration noted in the code.
- **"Already has an AI draft" means a `success` generation row exists.** An entry that has only ever had fallback text gets its first AI draft counted.
- **A refused Scan says "Free scans look back 90 days"**, rather than silently trimming the window in the API.

## Verification

1. Each fix lands with a failing test first: pytest for the API and workers, Jest for `limitMessage`, the reset format and the disabled states.
2. Extend `e2e/tier-limits.spec.ts`:
   - Free: Backfill grayed with the note; Past days and rule Apply days capped at 90;
   - read-only diary: SuggestionBox, EntryPlace, detach and Connect grayed;
   - a forced `tier_limit` 403 shows plain copy, never "tier_limit".
3. Worker tests:
   - a free redraft at 5 of 5 calls the provider and writes no spend;
   - a first draft at 5 of 5 doesn't call it;
   - two queue calls within the window enqueue one task, and a Regenerate in between doesn't reset the window;
   - when Redis is down, the draft is queued directly;
   - the sweeper releases an old orphan, keeps a fresh one, keeps one whose own key has a `success` row, and keeps one with a NULL entry or `sweepable=false`;
   - it also releases the killed S1 after a retry S2 succeeds;
   - a calendar-pick draft, untouched or still empty, is trashed on cancel, and a moved calendar-pick draft is left alone;
   - a read-only backfill ends `failed` with its message, and a cancelled one stays `cancelled`;
   - Scan `past_days=365` and Apply `days=91` are refused on Free, while 90 is allowed.
4. `make test-all` is green locally (baseline: pytest 1020, Playwright 67).
5. A manual check on the dev stack, using `./scripts/admin.sh users set-tier`:
   - Free: Backfill grayed; Scan with Past days 365 refused with the copy.
   - Plus: both work.
