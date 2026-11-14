# Review Fixes (Triage 2026-11-14, Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the triage items marked ⓐ, in code that is already on `main`.

**Architecture:** Each task is an independent bug fix with its own failing test first. There are no new services and no migrations. The work is:
- scan, backfill and draft workers: `apps/api/app/workers/`;
- refresh and login: `routers/v1/auth.py`;
- diary and entry responses;
- the entry page's place line in the web app.

**Tech Stack:**
- API: FastAPI 0.162.5, SQLAlchemy async, Celery, pytest. Python **3.12** after Task 1.
- Web: Next.js 15, Jest, Playwright.

**Spec:** `design/code-review-2026-11.md`, section "Triage (2026-11-14)". Rows TR-1 to TR-41 are the authority, and each task names its rows. Item 14's code (PR #51) is not on `main`; its fixes (ⓑ) are a separate plan on #51's branch.

## Global Constraints

- **Branch:** work on a branch off `main`. Don't touch files only PR #51 changes.
  - Leave the `refresh` code in `apps/web/src/lib/api.ts` alone; the client half of TR-29 belongs to Plan B.
  - Edit status cells only in Task 10.
- **Database sessions:** every `Depends(get_db)` uses `scope="function"`. The guard test `tests/unit/test_get_db_scope.py` enforces it.
- **Privacy:**
  - Coordinates in responses are owner-only (S-12).
  - Event addresses and attendees are owner-only (S-13).
  - Nothing new is logged that holds a token, an email address or event text.
- **AI drafts:** the LLM gets only facts from the source data, and drafts stay drafts.
- **Copy:** user-facing copy is plain, short English, never a raw error code.
- **Commits:** every message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Finish:** `make test-all` green, plus `cd apps/web && npx jest`.

## Review Focus

1. **A scan that fails on Google twice in a row, then succeeds.** The failures retry sooner than the hourly interval, back off, and reset once a scan succeeds. A *disconnected* calendar doesn't count as a failure: it needs the parent, and retrying faster can't fix it. Pinned in Task 3.
2. **A backfill started while the hourly scan holds the lock.** It waits and runs; it never reports "completed, 0 events". Pinned in Task 4.
3. **Two refreshes with the same cookie at the same instant**, as from two tabs or three uploads. Both succeed and the family ends with one live token. Pinned in Task 2.
4. **A co-parent opening diary settings.** They see the home town's name, not its coordinates, on every diary endpoint they can reach. Pinned in Task 7.
5. **The fallback draft and a model reply containing `**`.** No stars appear on the entry. Pinned in Task 6.

---

### Task 1: Local tests run what production runs (TR-39, P-12)

**Files:**
- Modify: `scripts/bootstrap-local.sh` (step 4, the Python env)
- Modify: `docs/local-dev.md` (prerequisites: `uv`)

**Interfaces:**
- Produces: `apps/api/.venv` on Python 3.12, built from `apps/api/requirements-dev.lock`. Every later task's pytest runs in it.

- [ ] **Step 1: Change step 4 of `bootstrap-local.sh`.**
  - If `uv` is missing, print this and `exit 1`:
    `uv is required: https://docs.astral.sh/uv/getting-started/installation/`
  - If `.venv` exists and `.venv/bin/python --version` isn't 3.12.x, remove it.
  - Then run, from `apps/api`:
    - `uv venv --python 3.12 .venv`
    - `uv pip sync --python .venv/bin/python requirements-dev.lock`
    - `uv pip install --python .venv/bin/python --no-deps -e .`
  - This matches `.github/workflows/ci.yml:31`: install from the hashed lock, then the package with no dependencies.
- [ ] **Step 2: Document it.** Add `uv` to the prerequisites table in `docs/local-dev.md`, with the link above. Add one line saying the venv is Python 3.12 from the lock, the same as CI and the image.
- [ ] **Step 3: Verify.**
  - **Controller only:** installing `uv` is a change to Andrew's machine. Ask Andrew to run the installer from the link, or to approve running it. The implementer never installs it.
  - Then run `./scripts/bootstrap-local.sh`.
  - Expected:
    - `apps/api/.venv/bin/python --version` prints `Python 3.12.x`;
    - `apps/api/.venv/bin/python -c "import PIL; print(PIL.__version__)"` prints `13.0.0`;
    - `make test-fast` passes.
- [ ] **Step 4: Commit.**
  ```bash
  git add scripts/bootstrap-local.sh docs/local-dev.md
  git commit -m "chore: local venv uses Python 3.12 and the lock, like CI (P-12)"
  ```

### Task 2: Two refreshes at once leave one live token (TR-29, server half)

**Files:**
- Modify: `apps/api/app/routers/v1/auth.py`: `refresh`, the lookup at about line 276
- Test: `apps/api/tests/integration/test_refresh_race.py` (new)

**Interfaces:**
- Produces: no interface change. `POST /v1/auth/refresh` answers as before.

- [ ] **Step 1: Write the failing test** `test_simultaneous_refreshes_leave_one_live_token`.
  - Log in once, then send two `POST /v1/auth/refresh` calls with the same cookie through `asyncio.gather`.
  - If the `client` fixture runs requests on one shared DB session, so they can't really overlap, drive two independent sessions against `refresh`'s query path instead, and say so in the report.
  - Assert:
    - both responses are 200 with an `access_token`;
    - `SELECT count(*) FROM refresh_tokens WHERE family_id = :f AND revoked_at IS NULL` is `1`;
    - a third refresh with the newest cookie is 200, not 500.
- [ ] **Step 2: Run it.** `pytest tests/integration/test_refresh_race.py -v`. Expected: FAIL, with a live-token count of 2 (or a 500 on the third call). If it passes on unchanged code, the test doesn't create overlap: fix the test before going on.
- [ ] **Step 3: Lock the row.** Lock the token row while refresh reads it: `.with_for_update()` on the `RefreshToken` select. The second request then waits, sees `revoked_at` set within `GRACE_SECONDS`, and takes the grace path.
- [ ] **Step 4: Run it.** Expected: PASS. Then run `pytest tests/integration -k "auth or refresh" -q` and expect all to pass.
- [ ] **Step 5: Commit** with `fix(api): refresh locks the token row so two refreshes can't fork a family (TR-29)`.

### Task 3: Failed scans retry sooner and back off (TR-2, TR-25)

**Files:**
- Modify: `apps/api/app/workers/tasks.py`:
  - the `scan_diary` decorator (about line 30);
  - the close-out block in `_scan_diary` (about lines 195-216);
  - `refresh_entry_location` and `refresh_diary_locations` (about lines 658-700).
- Test: `apps/api/tests/integration/test_scan_retry.py` (new)

**Interfaces:**
- Produces: `scan_backoff_seconds(failures: int, interval_minutes: int) -> int` in `tasks.py`. It returns `min(60 * 2**failures, interval_minutes * 60)`.

Ruling, for the reviewer:
- The retry mechanism is `ScanJob.next_scan_after`, which `dispatch_due_scans` checks every 5 minutes. It is not Celery's `self.retry`: the scan lock and the `ScanRun` bookkeeping make a Celery retry re-enter the middle of a run.
- So remove `max_retries=3, default_retry_delay=60` from `scan_diary`. It declares a retry that never happens.

- [ ] **Step 1: Write the failing tests.**
  - **`test_calendar_failure_counts_and_backs_off`:** mock `sync_calendar` to raise `httpx.ConnectError`. After `_scan_diary`:
    - `scan_job.consecutive_failures == 1`;
    - `next_scan_after - completed_at == timedelta(seconds=120)`.
  - **Second failure:** `consecutive_failures == 2` and the delay is 240 s.
  - **`test_unavailable_counts_as_failure`:** `ensure_fresh_access_token` returns `None` with the grant not revoked. That is the `calendar_unavailable` error, and it counts as a failure the same way.
  - **`test_disconnected_calendar_does_not_count`:** the token is revoked. Then `consecutive_failures` stays 0 and `next_scan_after` is the normal interval.
  - **`test_success_resets_failures`:** a clean scan after two failures leaves `consecutive_failures == 0` and the normal interval.
  - **`test_backoff_never_exceeds_interval`:** `scan_backoff_seconds(10, 60) == 3600`.
  - **`test_error_records_retry_count`:** the error dict's `retried_count` equals the failures before this scan.
  - **`test_refresh_entry_location_retries`:** `update_place_and_weather` raises. Calling `refresh_entry_location.apply(args=[id])` raises `Retry`, or shows `self.retry` was called, when patched. Do the same for `refresh_diary_locations`.
- [ ] **Step 2: Run them.** `pytest tests/integration/test_scan_retry.py -v`. Expected: FAIL, because failures stay 0.
- [ ] **Step 3: Implement.**
  - "Transient" means an error whose `error_class` isn't `calendar_disconnected` and whose `source` is `google_calendar`.
  - On a transient calendar error: increment `consecutive_failures` and set `next_scan_after = completed + scan_backoff_seconds(...)`.
  - On no calendar error: reset to 0 and use the normal interval.
  - On a disconnected calendar: leave the count unchanged and use the normal interval.
  - Write `retried_count` from the count before this scan.
  - The location tasks wrap their body in `try`/`except Exception as exc: raise self.retry(exc=exc)`, as `cancel_calendar_event` does (about line 385).
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests/integration -k "scan" -q` and expect all to pass.
- [ ] **Step 5: Commit** with `fix(api): failed calendar scans retry sooner with backoff; location tasks retry (TR-2, TR-25)`.

### Task 4: Backfill and scan counts tell the truth (TR-1, C-11)

**Files:**
- Modify: `apps/api/app/workers/backfill.py`: `_iter_week_chunks`, `run_backfill`
- Modify: `apps/api/app/workers/tasks.py`:
  - the backfill task wrapper (about lines 740-835);
  - `_scan_diary`;
  - `queue_due_rule_evaluations` (about line 446).
- Modify: `apps/api/app/routers/v1/scan.py:105`, which passes `triggered_by="manual"`.
- Test: `apps/api/tests/integration/test_backfill_worker.py` (existing), plus `tests/integration/test_scan_counts.py` (new).

**Interfaces:**
- Produces:
  - `scan_diary(diary_id, past_days=None, future_days=None, triggered_by: str = "beat")`.
  - `evaluate_due_events(diary_id: uuid.UUID) -> set[uuid.UUID]`. It replaces `queue_due_rule_evaluations`. It awaits `_evaluate_rules_for_event` for each due event, in process, and returns the distinct entry ids those events now belong to. If one event raises, the error is logged and that event is skipped. Its `rules_evaluated_at` stays NULL, so the next scan tries it again.
  - `class BackfillLocked(Exception)` in `backfill.py`.

How it works now, so the implementer knows (verified 2026-11-14):
- `ingest_calendar_event.delay(...)` returns a Celery `AsyncResult`, not an entry id. `_ingest_calendar_event` returns the **event** id (`tasks.py:275-371`).
- Entries are made later, by rule evaluation.
- Weather comes from `generate_entry_draft`, which calls `update_place_and_weather` (`tasks.py:630-643`). So the per-chunk weather loop in `run_backfill` is redundant as well as broken.

- [ ] **Step 1: Write the failing tests.**
  - **`test_chunks_do_not_overlap`:** `list(_iter_week_chunks(date(2026,1,1), date(2026,1,15)))` equals `[(1/1,1/7),(1/8,1/14),(1/15,1/15)]`. Each chunk is 7 days inclusive, and the next starts the day after.
  - **`test_backfill_counts_events_and_entries`:**
    - Two fetched events match one per-series rule, and one matches nothing.
    - The run ends `completed` with `events_ingested == 3` and `entries_created == 1`.
    - Assert `ingest_calendar_event.delay` is **not** called. Ingestion is awaited in process.
  - **`test_backfill_waits_when_scan_lock_held`:**
    - The lock is held. The run is still `pending`, not `completed`.
    - The task re-queued itself with `countdown=60`.
    - After 5 tries it is `failed` with error `"Another scan was still running. Try the backfill again in a few minutes."`.
  - **`test_backfill_has_no_weather_step`:** `enrich_entry_weather` isn't called by `run_backfill`.
  - **`test_scan_counts_entries`:** a scan whose due events make 2 entries records `ScanRun.entries_created == 2`.
  - **`test_manual_scan_is_marked_manual`:** `POST /v1/diaries/{id}/scan` queues `scan_diary` with `triggered_by="manual"`, and the run records `"manual"`.
- [ ] **Step 2: Run them.** `pytest tests/integration/test_backfill_worker.py tests/integration/test_scan_counts.py -v`. Expected: the new tests FAIL. Existing tests that mock `.delay()` to return an entry id now fail too: rewrite them to the new behaviour, and list each one in the report.
- [ ] **Step 3: Implement.**
  - Chunks: `cur = nxt + timedelta(days=1)`.
  - `run_backfill` awaits `_ingest_calendar_event` per event, then `evaluate_due_events` once per chunk. It returns `(events, len(all_entry_ids))`. When the lock is held it raises `BackfillLocked`.
  - The wrapper catches `BackfillLocked`, sets the run back to `pending`, and calls `self.retry(countdown=60, max_retries=5)`. Once retries are exhausted it marks the run `failed` with the message above.
  - `_scan_diary` uses `evaluate_due_events` and writes `entries_created = len(ids)` and `triggered_by`.
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests/integration -k "backfill or scan or rule" -q` and expect all to pass.
- [ ] **Step 5: Commit** with `fix(api): backfill and scan record real event and entry counts; backfill waits for the scan lock (TR-1)`.

### Task 5: "Past N days" works after the first sync (TR-4, C-16a)

**Files:**
- Modify: `apps/api/app/workers/calendar_sync.py` `sync_calendar` (lines 39-111)
- Test: `apps/api/tests/integration/test_calendar_sync_window.py` (new)

**Interfaces:**
- Produces: `sync_calendar(diary_id, access_token, diary_timezone, past_days: int | None = None, future_days: int | None = None) -> int`.
  - When either window argument is given, the call is a **windowed** fetch:
    - sync token `None`;
    - `timeMin`/`timeMax` from `_scan_window(past_days or DEFAULT_SCAN_PAST_DAYS, future_days or DEFAULT_SCAN_FUTURE_DAYS)`;
    - the stored `last_calendar_cursor` is left unchanged.
  - With neither given it behaves as today.

- [ ] **Step 1: Write the failing tests.**
  - **`test_window_ignores_and_keeps_sync_token`:**
    - `ScanJob.last_calendar_cursor = "tok-1"`.
    - Patch `_fetch_events` to record its arguments and return `([], "tok-2")`.
    - Call `sync_calendar(..., past_days=30)`.
    - Assert `_fetch_events` got `sync_token=None` with `time_min` about now − 30 days (±1 minute).
    - Assert the cursor is still `"tok-1"`.
  - **`test_no_window_uses_sync_token`:** with no window arguments, `_fetch_events` gets `"tok-1"` and the cursor becomes `"tok-2"`.
- [ ] **Step 2: Run them.** `pytest tests/integration/test_calendar_sync_window.py -v`. Expected: the first FAILS, because the token is used.
- [ ] **Step 3: Implement** as in the Interfaces block. `_scan_diary` already passes only the arguments it was given (`tasks.py:146-150`); keep that.
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests -k "calendar_sync" -q` and expect all to pass.
- [ ] **Step 5: Commit** with `fix(api): scan options' past/future days fetch that window without touching the sync token (TR-4)`.

### Task 6: Draft text: fewer false warnings, no attendee emails, no Markdown (TR-7, TR-8, TR-9)

**Files:**
- Modify: `apps/api/app/workers/llm.py`:
  - `_unsupported_words` (line 298);
  - `validate_citation` (line 306) and its caller (about line 589);
  - `_format_event_line` (lines 174-182);
  - `_build_fallback_body` (about line 402);
  - the system prompt rules (lines 40-80).
- Test: `apps/api/tests/unit/test_llm_text_rules.py` (new)

**Interfaces:**
- Produces:
  - `validate_citation(output, events, mode="events", body_seed="", enrichments=None, known_words: str = "")`.
  - The caller passes `known_words=diary.subject_name or ""`.
  - Module constant `_SENTENCE_STARTERS: frozenset[str]`.

The rules:
- A capitalised token is **not** flagged when any of these is true:
  - its lowercase form appears in the lowercased source text plus `known_words`;
  - it is in `_CALENDAR_WORDS`;
  - it starts a sentence (start of text, or after `. `, `! `, `? ` or a newline) **and** is in `_SENTENCE_STARTERS`.
- `_SENTENCE_STARTERS` is about 60 common English sentence openers: articles, pronouns, time words, connectives. Examples: The, A, An, After, Before, Then, Today, Later, First, Next, Finally, This, That, These, It, She, He, They, We, I, You, In, On, At, During, While, When, Afterwards, Everyone, There, Once. It holds no names and no nouns, so an invented "Grandma" at the start of a sentence is still flagged.
- Attendees: a name is used only when `displayName` is present. Nameless attendees become `and N other` / `and N others`; when nobody has a name, the line reads `N people` (`1 person`). No email address appears in the prompt.
- The fallback body lines become `- {time_range}  {summary}{loc_suffix}`, with no `**`.
- The prompt gains one rule: `Write plain sentences. Do not use Markdown (no **, #, or _ formatting).`

- [ ] **Step 1: Write the failing tests.**
  - `test_subject_name_not_flagged`: body `"Mia played soccer."`, source with no "Mia", `known_words="Mia"`, gives `[]`.
  - `test_sentence_starter_not_flagged`: `"After lunch they swam."` gives `[]`.
  - `test_invented_name_at_sentence_start_flagged`: `"Grandma visited."` gives `["Grandma"]`.
  - `test_case_insensitive_match`: the source has "soccer park" and the body "Soccer Park"; nothing is flagged.
  - `test_nameless_attendees_never_send_email`: attendees `[{displayName:"Ana"},{email:"x@y.com"},{email:"z@y.com"}]` give `attendees: "Ana and 2 others"`, and `"@"` is not in the line.
  - `test_all_nameless_attendees`: one email-only attendee gives `attendees: "1 person"`.
  - `test_fallback_body_has_no_markdown`: `"**" not in body`.
  - `test_prompt_forbids_markdown`: the system prompt contains `Do not use Markdown`.
- [ ] **Step 2: Run them.** `pytest tests/unit/test_llm_text_rules.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests -k "llm or citation or fallback" -q` and expect all to pass. Existing tests that asserted the old `**` or an email in the line are updated; list them in the report.
- [ ] **Step 5: Commit** with `fix(api): fewer false "verify" words, no attendee emails to the model, plain-text drafts (TR-7, TR-8, TR-9)`.

### Task 7: Diary home coordinates are owner-only (TR-20)

**Files:**
- Modify: `apps/api/app/routers/v1/diaries.py`: `DiaryOut` (line 77) and every handler returning it:
  - `list_diaries` (157), create (175), trash (240), `get_diary` (257), patch (267), delete (304), restore (321).
- Test: `apps/api/tests/integration/test_diary_coordinates_privacy.py` (new)

**Interfaces:**
- Produces: `_diary_out(diary: Diary, *, is_owner: bool) -> DiaryOut`. It blanks `lat` and `lon` to `None` when `is_owner` is false and keeps `location_name`. This mirrors `exact_places` in `entries.py:277-282`.

- [ ] **Step 1: Write the failing test.** `test_co_parents_never_get_home_coordinates`, parametrized over `viewer` and `editor`.
  - The owner sets the home place (lat 47.6, lon −122.3, "Seattle").
  - For the co-parent:
    - `GET /v1/diaries` (the shared entry) and `GET /v1/diaries/{id}` give `lat is None and lon is None and location_name == "Seattle"`.
    - If editors may PATCH a diary, the PATCH response is blanked too. Check the handler's role rule and state it in the test.
  - The owner's own `GET` still has 47.6 and −122.3.
- [ ] **Step 2: Run it.** `pytest tests/integration/test_diary_coordinates_privacy.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The handlers return `_diary_out(...)` instead of the ORM object. Owner-only handlers pass `is_owner=True`.
- [ ] **Step 4: Run it.** Expected: PASS. Then run `pytest tests -k "diar" -q` and expect all to pass. Also check the web settings page handles `lat: null` for a co-parent: `apps/web/src/app/diaries/[diaryId]/settings/` and its tests.
- [ ] **Step 5: Commit** with `fix(api): diary home coordinates go to the owner only (TR-20)`.

### Task 8: Place and time-zone fixes (TR-19, TR-21, TR-22, TR-24, TR-26)

**Files:**
- Modify:
  - `apps/api/app/services/geocoding.py` `search_places` (line 116);
  - `apps/api/app/routers/v1/geocode.py:39`;
  - `apps/api/app/routers/v1/entries.py`: the PATCH, lines 548-554;
  - `apps/api/app/workers/rules.py`: `evaluate_event_against_rules` and the claim return at line 230;
  - `apps/web/src/components/EntryPlace.tsx:79`;
  - `apps/web/src/components/PlaceSearch.tsx` (the loading state);
  - `apps/web/src/app/entries/[entryId]/page.tsx:22`.
- Test:
  - `tests/integration/test_entry_place_fixes.py` (new);
  - `apps/web/src/components/__tests__/EntryPlace.test.tsx`;
  - the `PlaceSearch` test;
  - an entry page time-label test.

**Interfaces:**
- Produces:
  - `search_places(query, *, count=5, country_code=None, timeout: float | None = None)`. `None` means the setting's 30 s. `/v1/geocode` passes `timeout=5`.
  - `_entry_for_event` reports when an event joined an **existing** claimed entry, as a third tuple field or a flag. The implementer chooses and states it.
  - After commit, `evaluate_event_against_rules` calls `queue_location_refresh(entry_id)` for a joined entry that isn't getting a draft.

- [ ] **Step 1: Write the failing tests.**
  - **TR-19:**
    - `test_geocode_endpoint_uses_short_timeout`: patch `httpx.AsyncClient` and assert `timeout=5`.
    - Jest: `PlaceSearch` shows `Searching…` while the request is pending.
  - **TR-21:** `test_patch_date_and_tz_keeps_the_typed_date`. PATCH `{entry_date: "2026-06-02", tz_override: "Asia/Tokyo"}` on an entry whose events would move it to 2026-06-03 returns `entry_date == "2026-06-02"`. PATCH with only `tz_override` still moves the date, as today.
  - **TR-22:** `test_event_joining_claimed_entry_refreshes_place`. The second event of a recurring series joins the claimed entry, and `queue_location_refresh` is called once with that entry id.
  - **TR-24:** Jest. A location with `name: null` reads:
    - `From a photo` for `source: 'photo'`;
    - `Home` for `'home'`;
    - `From the event` for `'event'`;
    - `Your place` for `'parent'`.
  - **TR-26:** Jest. With the time zone `America/Los_Angeles`, an event on 2026-07-04 shows `PDT` and one on 2026-01-04 shows `PST`, whatever today's date is. Mock `Date` or pass the date in.
- [ ] **Step 2: Run them.**
  - `pytest tests/integration/test_entry_place_fixes.py -v`
  - `cd apps/web && npx jest EntryPlace PlaceSearch entries`
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
  - TR-21: recompute dates from events only when neither `entry_date` nor `entry_end_date` is in `updates`.
  - TR-26: the time-zone name comes from `formatToParts` on the **event's start date** instead of `new Date()`.
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests -k "place or geocode or rule or entry" -q` and `npx jest`, and expect all to pass.
- [ ] **Step 5: Commit** with `fix: place search timeout and spinner, typed date kept, claimed entries refresh place, place and zone labels (TR-19, 21, 22, 24, 26)`.

### Task 9: Logging in during the deletion window offers restore (TR-3, C-14)

**Status: waiting on Andrew.** The web app has no "delete account" control today, so deletion is API-only and no parent can reach this yet. Andrew decides whether this task runs now or moves to "Later, with account settings". If it moves, delete this task and mark TR-3 "Later" in Task 10.

**Files:**
- Modify: `apps/api/app/routers/v1/auth.py`:
  - `login` (line 163);
  - Google login (line 209);
  - `TokenResponse`.
- Create: `apps/web/src/app/account/restore/page.tsx`
- Modify: `apps/web/src/app/login/page.tsx`
- Test:
  - `tests/integration/test_restore_on_login.py`;
  - `apps/web/src/app/account/restore/__tests__/page.test.tsx`.

**Interfaces:**
- Produces: `TokenResponse.account_pending_deletion: bool = False` and `hard_delete_after: datetime | None = None`.
  - For an account within its window (`deleted_at` set and `hard_delete_after > now`), `login` issues tokens with those fields set. Every other endpoint still refuses the account, because `get_current_user` is unchanged.
  - Past the window it stays 401 `account_unavailable`.

- [ ] **Step 1: Write the failing tests.**
  - API:
    - in the window, password login gives 200, `account_pending_deletion is True`, and `POST /v1/auth/account/restore` with that token gives 204;
    - after restore, `GET /v1/diaries` gives 200;
    - past the window, login gives 401;
    - the same holds for the Google login path.
  - Jest: the login page routes to `/account/restore` when the flag is set. The restore page shows `Your account is set to be deleted on {date}.` with two buttons:
    - `Restore my account` calls restore, then goes to `/diaries`;
    - `Keep it deleted` logs out.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run them.** Expected: PASS. Then run `pytest tests -k "auth or account" -q` and `npx jest`, and expect all to pass.
- [ ] **Step 5: Commit** with `fix: logging in during the deletion window offers restore (TR-3)`.

### Task 10: Docs, status cells and the full suite

**Files:**
- Modify: `design/code-review-2026-11.md`.
  - Set the status cell of each finding this plan fixed to `Fixed, tested (<test file>)`, with the TR number: C-11, C-14 (if Task 9 ran), C-16, C-17, L-5, and the design-drift bullets for scan retries and Markdown.
  - Close S-10 and L-4 with the triage reason.
  - Mark C-16's third part fixed (item 14).
- Modify: `design/03-api-surface.md` (diary coordinates owner-only; scan `triggered_by`; backfill counts and the lock wait; the login restore flag if Task 9 ran) and `docs/local-dev.md`, if Task 1 didn't already cover it.

- [ ] **Step 1: Update the docs** as listed.
- [ ] **Step 2: Run** `make test-all` and `cd apps/web && npx jest`. Expected: all green. Record the counts.
- [ ] **Step 3: Commit** with `docs: review fixes from the 2026-11-14 triage`.
