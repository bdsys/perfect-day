# Item 18 follow-ups (tier enforcement)

Written 2026-11-15, after item 18 merged to `main` (#53 → #51, `066ee7d`). This file is the durable list of the following:
- Andrew's decisions after the build;
- the fixes still to make (PoC item **18.1** in `POC_PHASE2_TODO.md`);
- what is deferred;
- the backlog of minor findings.

The original findings, worded as the reviewers wrote them, are in #53's description: <https://github.com/bdsys/perfect-day/pull/53>.

Spec: `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md`.

## Decisions (Andrew, 2026-11-15)

These change the spec. Its "Decisions after the build" section points here.

1. **Automatic redrafts are free.** A background redraft spends no AI draft. It runs when a calendar event an entry is built from changes, is cancelled or moves, and it runs without the parent clicking anything.
   - **Claude's reading, for Andrew to confirm:** what's free is a *redraft* of an entry that already had its draft.
     - The first automatic draft of a new entry still counts (scan, rule, "New entry from Google Calendar").
     - Generate, Polish and Regenerate still count.
     - Otherwise the Free plan's "5 AI drafts a month" would cap nothing.
   - **Code today:** `generate_draft_for_entry` reserves for every trigger (`workers/llm.py`). The `trigger="background"` redrafts queued by `workers/event_changes.py` need to skip the reservation, and the at-limit branch must not block them.
   - Watch the cost: a free redraft is still an LLM call. A calendar that churns could cost money with no cap. Consider a per-entry or per-day ceiling on free redrafts. That is an open question for the fix plan.
2. **Inviting stays allowed on a read-only diary.** No change: invitation and permission endpoints are not guarded, and revoking access was always allowed.
3. **Backfill becomes a paid feature** (Plus and Family). Free parents can't backfill.
   - The "Backfill" control on the diary page is grayed out for Free, with a note and **Upgrade now**.
   - `POST /v1/diaries/{id}/scan/backfill` returns 403 `tier_limit` with `source: "backfill"`. That is a new `LimitSource` value, and `required_tier` is `plus`.
   - This removes the "a backfill on the 1st can spend all 5 drafts on old history" concern.
   - **Open for the fix plan:**
     - Does the rule "apply to past events" action (`apply_rule_backfill`, `POST …/rules/{id}/apply`) count as backfill? Claude's suggestion: yes, since it drafts past history.
     - Does the 90-day first-scan window stay free? Claude's suggestion: yes. It's the normal first scan, not a backfill.
     - `COMPETITION.md`'s plan table needs a "Backfill" row.

### Answers to the open questions (Andrew, 2026-11-15, second round)

The 18.1 design records these as D1–D6: `docs/superpowers/specs/2026-11-15-tier-followups-design.md`.
- **Which drafts are free:** any automatic redraft of an entry that already has an AI draft. That covers an event cancelled or moved, and a new event joining the entry. Redrafting on an *edited* event doesn't exist yet; it's backlog row 33. The first automatic draft still counts. The code's `trigger="background"` also labels first drafts (`workers/rules.py:171`, `routers/v1/calendar_events.py:152`), so "free" is decided by whether the entry already has an AI draft, not by the trigger.
- **Cost ceiling:** coalesce, with at most one free redraft per entry per ~10 minutes and no monthly cap.
- **Backfill scope:** the date-range Backfill is paid. Free looks back at most 90 days everywhere:
  - Rule "apply to past events" is capped at 90 days on Free, not fully paid. It only re-checks events already stored (`workers/tasks.py:661-668`), so a fully paid Apply would stop a Free parent's new rule from ever matching their own last 90 days.
  - A Scan with "Past days" over 90 is paid: the Scan ⚙ menu allowed up to 3650 days (`[diaryId]/page.tsx:432-439`, `routers/v1/scan.py:25`), which was a free backfill. The normal 90-day scan stays free. Future days are not limited.
- **Inviting:** no change (decision 2).
- **18.1-g:** an untouched "New entry from Google Calendar" draft whose event is cancelled goes to Trash, as rule drafts already do.
- **This doc branch** is folded into `feat/18.1-tier-followups`, so everything ships in one PR.

## To fix — PoC item 18.1 (built)

These are visible to a parent. Each fix needed its own failing test first. All nine rows are built on `feat/18.1-tier-followups`; the Status column lists the commits. Design: `docs/superpowers/specs/2026-11-15-tier-followups-design.md`.

| # | What | Where | Fix | Status |
|---|---|---|---|---|
| 18.1-a | Some limit refusals show the raw word "tier_limit". It appears when accepting a suggestion, editing the place or removing a photo on a read-only diary, and when attaching a second photo from a stale page. A regenerate 403 on a read-only diary also shows the AI note instead of the read-only one. | `apps/web/src/lib/api.ts` `parseApiError` (message = code) and every call site that doesn't special-case `tier_limit` | One helper that maps `tier_limit` + `details.source` (`ai_draft`, `photo`, `diary`, `diary_read_only`, and later `backfill`) to the copy strings in `lib/plan.ts`. Use it wherever an action can be refused. | done: `18eba20`, `2260250` |
| 18.1-b | On a read-only diary, SuggestionBox accept, EntryPlace edit, photo detach (entry page) and Connect Google Calendar (diary page) stay enabled. The API refuses them. | `apps/web/src/app/entries/[entryId]/page.tsx`, `components/SuggestionBox.tsx`, `components/EntryPlace.tsx`, `apps/web/src/app/diaries/[diaryId]/page.tsx` | Gray them out when `plan.read_only`, as the other controls are. | done: `673963c` |
| 18.1-c | A backfill queued just before a diary turns read-only stays "pending", with a spinner, until the parent presses Cancel. | `apps/api/app/workers/tasks.py` `_backfill_diary` read-only skip | Set the run to a finished state with a plain message before returning. | done: `0e28728` |
| 18.1-d | If a worker is killed mid-draft (for example by the NUC's out-of-memory killer), the reservation stays with no AI text, and the parent loses 1 draft. | `apps/api/app/services/ai_drafts.py`; Celery's default `task_reject_on_worker_lost=False` | A sweeper (beat task) that releases reservations older than about 15 minutes whose entry has no successful `llm_generations` row after the reservation time. The reviewer advised against `task_reject_on_worker_lost=True` globally, because a crashing task would loop. | done: `65cdec3`, `aeafdfc` |
| 18.1-e | The reset date reads "· resets" on the diary list badge but ", resets" on the diary and entry pages. | `apps/web/src/app/diaries/page.tsx`, `[diaryId]/page.tsx`, `entries/[entryId]/page.tsx` | Use one format: "· resets {date}", matching the badge itself. | done: `18eba20` |
| 18.1-f | The diary restore page's limit note says "Free" if the plan lookup fails after the 403. | `apps/web/src/app/diaries/restore/page.tsx` | Take the plan from `details.required_tier`/`limit`, or say "Your plan". | done: `18eba20` |
| 18.1-g | An entry that loses *all* its events keeps its old AI text. This is older than item 18: the `no_inputs` path never touches text. | `apps/api/app/workers/llm.py` (`no_inputs` branch) | For machine-only text (`llm` or `fallback`) with no events left, clear it or replace it with a short "This entry's events were removed" note. That is a product call for the fix plan. | done, changed: `c4ad049`, `ba17dff`. The NULL-body case was dropped, because `body_source` is NOT NULL (default `llm`). Untouched calendar-pick drafts go to Trash when their event is cancelled. |
| 18.1-h | Decision 1: background redrafts are free. | `workers/llm.py`, `services/ai_drafts.py` | See Decisions. | done: `9951bae`, `c2971f6`, `9c8be11`, `5540dcf` |
| 18.1-i | Decision 3: backfill is paid only. | `routers/v1/scan.py`, `routers/v1/rules.py` (apply), `services/tier.py`, the diary page, `COMPETITION.md` | See Decisions. | done, changed: `242df21`, `0e28728`, `673963c`, `12ec03b`. The rule Apply control keeps its 7/30/90 select, which never exceeds 90. |

## Deferred (agreed 2026-11-15): performance on the NUC

Revisit once the NUC is live and there are real numbers.
- `evaluate_event_against_rules` adds about 3 small queries per evaluated event for the read-only check (`workers/rules.py`). A per-run cache would remove them.
- Diary and entry detail GETs add 3–4 small queries for `plan` (`services/plan_view.py`), and `ai_draft_usage` queries usage even on unlimited plans.
- `ensure_photo_room` counts twice, and the import create route counts the room twice (`services/photo_limits.py`, `routers/v1/photo_imports.py`).
- `_can_still_edit` makes an extra `db.get(Diary)` round trip (`workers/photo_imports.py`).

## Backlog: minor findings, no behaviour change (agreed 2026-11-15)

**Tests that could be stronger or are missing**
- No positive control for the read-only worker skips: a draft in the writable diary should call the provider. There are no tests for the rules and backfill worker skips, or for unguarded routes beyond entry and diary delete (diary restore, backfill cancel, import cancel/dismiss, `DELETE /photos`).
- The release-on-exception backstop in `generate_draft_for_entry` is untested. The keep/release split on the suggestion path is untested.
- The timezone test has no "oldest diary / soft-deleted older diary in another zone" case.
- The editor-quota worker test is trivially true. A stronger version: a Free owner at 5 of 5 with a Family editor, asserting the provider is not awaited.
- No regenerate test for a Plus owner, and no test that read-only wins over the AI gate.
- `test_rule_creates_entry_past_old_auto_limit` could assert 4 automatic entries in total.
- No `limitColor(0, 0)` test.

**Code tidiness**
- The `rules.py` `_entry_for_event` docstring has a run-on line.
- `reserve_ai_draft` still runs its own `limits_for` → `usage_period` → `drafts_used` sequence instead of `ai_draft_usage`. Its arithmetic can't disagree.
- In `llm.py`, the read-only check sits after the published/background `updated_at` touch, so it makes one harmless write.
- The entry page has a redundant `disabled` on Add photos, and writes the plan ref during render. That works under StrictMode, but a state/effect is more idiomatic.
- Unrelated ruff reformatting noise in `routers/v1/entries.py`.
- `test_photo_limits.py` has a redundant `_set_tier(free)`. `test_manage_cli.py` sets `sys.modules["manage_cli"]` without cleanup.
- `formatResetDate` has no guard for malformed input. The API controls that input.

**Accessibility and UI polish**
- The Scan and Backfill buttons' `aria-describedby` doesn't point at the read-only banner.
- The `/plans` page has an empty `<td/>` header cell, and its "← Diaries" link assumes the reader is signed in.
- At room 0, AddPhotos hides its whole panel, including in-flight rows. On Free only one file can be picked, so nothing in flight is lost.

**Accepted as is**
- A redelivery after a saved draft that then fails releases by key, so the parent is under-counted by 1, in their favour. This happens only in a crash window.
- Importing an already-attached photo onto a full entry ends `done`, not as a duplicate. It has no visible effect.
- The `/plans` page keeps its own copy of the limits (Ruling R6) until billing adds a public `GET /v1/plans`.

**Already fixed in the final fix wave (listed so nobody re-opens them)**
- At the limit, stale machine-only text now gets the event list (F1).
- The photo race test now fails without `FOR UPDATE` (F2).
- The restore pages' copy and link (F3).
- The badge details and ARIA on the diary list (F4).
- Viewers no longer receive the owner's plan (F5).
- design/03, 06, 07 and code-review-2026-11 are corrected (F6).
- The `restore_entry` unused `diary` variable is now used by the read-only guard.
