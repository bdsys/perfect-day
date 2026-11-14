# Code Review — November 2026

Whole-codebase review of `main` at `2c984b6` (Phase 1 plus Phase 2 items 13, 15, 16, 17, 19, 22, 23), tracked as item 25 in [`POC_PHASE2_TODO.md`](../POC_PHASE2_TODO.md). Covers `apps/api/app`, `apps/web/src`, the Alembic migrations, `scripts/` and the deploy files, read against the design docs in this folder.

Fixes live on branch `chore/code-review-2026-11`.

## How to read the status column

| Status | Meaning |
|---|---|
| **Fixed, tested** | Changed on the branch and covered by a test written for it (or, for shell scripts, a local harness). The integration tests in `tests/integration/test_review_fixes.py` were each run against the pre-fix code and seen to fail. |
| **Fixed, untested** | Changed on the branch with no test written for the fix itself. The existing suites pass with the change in place (see below), which shows nothing regressed; it does not show the fix works. |
| **Open** | Not changed. Either it needs a product or deployment decision, or it could not be verified without the NUC. |

`make test-all` passed on the branch on 2026-11-11: lint, type checks, 482 API unit and integration tests (3 skipped: the live-LLM tests, which need API keys), and 39 browser tests (1 skipped: a weather-badge test that was already disabled). The run also built the API and web images. It does not exercise the NUC scripts or the deploy workflow.

Every finding was confirmed by reading the code. Three were also reproduced with a script: the AES-GCM keystream reuse (S-1), the unconditional `DELETE FROM magic_link_tokens` (D-1), and the rollback of writes made before an `HTTPException` (S-2).

---

## Security

| ID | Finding | Where | Status |
|---|---|---|---|
| S-1 | **Photo and thumbnail share an AES-GCM key and nonce.** Both were encrypted under the same DEK, and chunk nonces depended only on the chunk index. Anyone who can read MinIO can XOR the two blobs to get the XOR of the plaintexts, and can forge ciphertexts. The design doc itself specified "thumbnails encrypted with the same DEK". New blobs use the `PD02` format: a subkey per blob, and the header plus chunk index bound in as associated data (which also stops truncation by editing `chunk_count`). `PD01` blobs still decrypt. **Existing thumbnails keep the weakness until re-encrypted** — no migration script exists yet. | `core/photo_crypto.py`, `routers/v1/photos.py` | Fixed, tested |
| S-2 | **Stolen refresh-token detection never took effect.** The token-family revocation ran, then the handler raised `HTTPException`, and `get_db` rolls back on any exception. | `routers/v1/auth.py` `refresh` | Fixed, tested |
| S-3 | **Next.js 15.1.0 is affected by the React Server Components remote-code-execution flaw** (CVE-2025-55182 / CVE-2025-66478, App Router). Upgraded to 15.1.12; `next build`, type check, lint and Jest pass. Source: <https://nextjs.org/blog/CVE-2025-66478> | `apps/web/package.json` | Fixed, tested |
| S-4 | **Prompt delimiter breakout.** Calendar text was placed inside `<event>` tags unescaped, so a title containing `</event>` ended the data region early. Same for `<draft_body>`. Delimiter tags in user text are now neutralised, and descriptions are capped at 2,000 characters. | `workers/llm.py` | Fixed, tested |
| S-5 | **Google connect is not bound to the browser session.** The OAuth `state` carries the initiating user's id and a nonce, but the callback never checks that the browser completing consent is that user's. An attacker who sends a victim their authorize link gets the victim's calendar attached to the attacker's account. Fix: set a cookie on the API origin when the authorize URL is issued and require it in the callback. | `routers/v1/integrations.py` | Open |
| S-6 | **Rate limits are shared by everyone.** The limiter's key function runs before authentication, so it always falls back to the remote IP, and behind Caddy that IP is Caddy's (uvicorn only trusts `X-Forwarded-For` from 127.0.0.1 by default). All users share one 100/min bucket, and ten bad logins a minute lock out login and register for everyone. Fix needs a deployment choice: key on `CF-Connecting-IP`, and set `--forwarded-allow-ips`. | `middleware/rate_limit.py`, `docker-compose.yml` | Open |
| S-7 | **Uploads sit unencrypted in MinIO until finalized**, under `tmp/`, for up to 24 hours if the client never calls finalize (still open). The size cap was also not enforced: finalize read the whole object into memory without checking its size. Finalize now checks the stored size first and rejects oversize uploads. | `routers/v1/photos.py`, `services/photos.py` | Size check fixed, tested; plaintext `tmp/` open |
| S-8 | **Disconnecting Google only sets a local flag.** The grant is not revoked at Google and the encrypted tokens stay in the database. | `routers/v1/integrations.py` `revoke_google` | Open |
| S-9 | **Diary limit lock used Python `hash()`**, which is salted per process, so two API processes would never contend on the advisory lock. Now uses Postgres `hashtext`. Latent today (single process). | `routers/v1/diaries.py` | Fixed, untested |
| S-10 | `/readyz` returned raw exception text to anonymous callers (now logs it and returns `error`; tested). `require_reauth` called `run_until_complete` inside a running loop (now an async dependency; tested). `python-jose` floor raised from 3.3 to 3.4, the release that fixes CVE-2024-33663/33664. The refresh `Origin` check accepts a missing header; closed as won't fix (TR-13): the refresh cookie is `SameSite=Strict`, so a cross-site request never carries it. | `routers/health.py`, `core/auth.py`, `pyproject.toml` | Closed, won't fix (TR-13) |
| S-11 | **Every co-parent can read the owner's synced calendar.** Each scan copies all events from the owner's chosen calendars (default: primary) for the scan window into the diary, not only events that became entries. `GET /v1/diaries/{id}/calendar-events` (the "New entry from Google Calendar" list) checks only membership, never role, so a view-only co-parent sees every event's title, description, location and attendees. This undercuts the 2026-11-12 decision to keep scanning owner-only for the owner's privacy. Found 2026-11-12 (side-agent note, confirmed in code). | `routers/v1/calendar_events.py` (`list_calendar_events`, `create_entry_from_event`) | Fixed, tested (`TestSyncedCalendarIsOwnerOnly`, browser test) — decision 3 below |
| S-12 | **Every co-parent received each photo's exact GPS.** `PhotoOut` in entry responses carried the photo's `lat`/`lon` (accurate to metres) to anyone who could see the entry, view-only family included. A photo taken at home or at school gives out that address. The web never showed them, but they were in the data. Item 24 would have added the same for the entry's place. Found 2026-11-12 (side-agent note, confirmed in code). | `routers/v1/entries.py` (`_entry_out_from_orm`) | Fixed, tested (`TestCoordinatesStayWithTheOwner`): coordinates go to the diary owner only |
| S-13 | **Viewers see each event's full location text.** `EntryOut.events` gives anyone who can see an entry its events' `location` strings (street addresses, venue names), and their attendees. Item 24 shows only the town for the entry's place, but the event list still carries the address. Found 2026-11-12 in the item 24 final review; predates item 24. | `routers/v1/entries.py` (`_event_out_from_orm`) | Fixed, tested (`test_event_privacy.py`): event location text and attendees go to the diary owner only — decision 4 below. PR #49 |
| S-14 | **Any co-parent can search the owner's synced calendar through the rule preview.** `POST /v1/diaries/{id}/rules/preview` checks only membership, never role, so a view-only co-parent can run it although viewers cannot create rules. It matches every synced event of the last 90 days, including events that never became entries, and returns a count and up to 10 titles and dates. Its conditions can test `location` and `attendee_email`, so anyone who can call it can check whether a given address or email appears in the calendar. This undercuts decision 3 (the synced calendar is the owner's alone). Found 2026-11-12 while fixing S-13. | `routers/v1/rules.py` (every endpoint), diary page, rules page | Fixed, tested (`test_rules_owner_only.py`, browser tests in `calendar-connection.spec.ts`): every rule endpoint is owner-only — decision 5 below |

## Data loss and integrity

| ID | Finding | Where | Status |
|---|---|---|---|
| D-1 | **Hard-deleting one user deleted every user's magic-link tokens.** The `delete(MagicLinkToken).where()` had no condition. Now matches the user's email. | `workers/hard_delete.py` | Fixed, tested |
| D-2 | **Hard-deleting a diary deleted photos still used by other diaries.** Photos are a user-wide library; the code removed every photo attached to the diary's entries with no exclusivity check. Now skips photos attached to entries elsewhere. | `workers/hard_delete.py` | Fixed, tested |
| D-3 | One failing hard delete aborted the whole run, and so every later run. Each deletion is now isolated and logged. | `workers/beat_tasks.py` | Fixed, tested |
| D-4 | **A slow generation could overwrite an entry published or deleted while it ran.** The write step now rechecks and discards the result. Deleted entries no longer reach the provider. | `workers/llm.py` | Fixed, tested |
| D-5 | **Re-running `10-secrets.sh` replaced `MASTER_SECRET`**, `OAUTH_TOKEN_SECRET`, `SECRET_KEY` and the database password with no check or backup — and the script's own output told the operator to re-run it to add DDNS. After that every stored photo and OAuth token is undecryptable. The script now keeps existing secrets, backs the file up first, and lets a blank answer keep the current value. Also fixes GitHub issue #39 (prompt helpers prepended a newline to every value) and writes the DDNS JSON with `jq`. Tested with a local harness that fails on the old script; not run on the NUC. | `scripts/nuc/10-secrets.sh` | Fixed, tested locally |
| D-6 | **Backups could not run and never include photos.** The backup called `pg_dump -h localhost` on the host, but Postgres publishes no host port and `00-bootstrap.sh` installs no Postgres client. The dump now runs inside the postgres container and is written to a `.partial` file first, so a failed run cannot leave a truncated file that gets uploaded. **Still open: MinIO photo objects are not backed up at all.** Not run on the NUC; do a test restore. | `scripts/nuc/30-backup.sh` | Dump fixed, untested; photo backup open |
| D-7 | Accounts in the 7-day deletion window kept being scanned and drafted. Scans now skip them, and account deletion revokes the Google tokens as the design specifies. | `workers/beat_tasks.py`, `workers/tasks.py`, `routers/v1/auth.py` | Fixed, tested |
| D-8 | **Writes are committed after the response is sent.** `get_db` commits after its `yield`; since FastAPI 0.118 that code runs after the response goes out (source: <https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/>; we run 0.136.3). A client can get "success" before the data is saved: if the commit then fails, the success was false, and a request sent straight after can miss the write. Likely cause of the e2e flake where `POST /v1/diaries` returns 401 right after a successful register (API logs show register 201 then 401 on the same connection, under parallel test load). Not reproduced on demand: 0 stale reads in 200 PATCH-then-GET tries. API tests cannot see it because the test client waits for the whole request. Fix: `Depends(get_db, scope="function")`, which ends the dependency before the response. Found 2026-11-12. | `core/database.py`, every `Depends(get_db)` | Fixed, tested (`test_commit_before_response.py` reproduced it every time: response before commit, and 201 for a failed save; `test_get_db_scope.py` guards it). PR #46 |

## Correctness

| ID | Finding | Where | Status |
|---|---|---|---|
| C-1 | **Calendar sync stopped permanently after a sync token expired.** On HTTP 410 the dead token was never cleared, so every later scan got 410 again and reported success with zero events. Now falls back to a full sync and stores the fresh token. | `workers/calendar_sync.py` | Fixed, tested |
| C-2 | **A calendar that kept answering 429 looped forever** while the heartbeat kept the scan lock alive. Waits are capped; `Retry-After` given as a date no longer crashes. | `workers/calendar_sync.py`, `workers/backfill.py` | Fixed, tested |
| C-3 | **Any non-200 from Google's token endpoint permanently disconnected the integration**, including 5xx and 429. Only `invalid_grant` revokes now. | `workers/token_refresh.py` | Fixed, tested |
| C-4 | **Background jobs were queued before their rows were committed.** A worker that picked the task up first found nothing and returned silently: drafts never generated, backfill runs stuck "pending". Fixed in the rules engine, the backfill endpoint and create-from-event. `trigger_scan` has the same shape (the worker can read the old `next_scan_after` and skip) and is still open. | `workers/rules.py`, `routers/v1/scan.py`, `routers/v1/calendar_events.py` | Fixed, tested |
| C-5 | **Regenerating treats the previous AI draft as the diarist's own writing.** Mode is chosen from "is the body non-empty", ignoring who wrote it. Regenerating an AI or fallback draft therefore runs in hybrid mode, which tells the model the old draft is "the diarist's lived experience", skips the token check, and clears the "verify before publishing" warning. Any fabrication in the first draft becomes permanent. Fix needs a migration: add `user` to `entries.body_source`, set it when a person writes or edits the body, and pick `events` mode for machine-written bodies. | `workers/llm.py`, `routers/v1/entries.py` | Fixed, tested (25.3) |
| C-6 | **Entries are drafted for events that have not happened.** Scans ingest 90 days ahead and rules create and draft entries for those events immediately. A diary can narrate a soccer game that is later cancelled. Also, once an event is ingested, a later cancellation or reschedule is never applied (`occurred_at` is not updated; cancelled events are skipped rather than removed). Needs a decision on intended behaviour. | `workers/calendar_sync.py`, `workers/tasks.py`, `workers/rules.py` | Fixed, tested (25.1) |
| C-7 | **Only the newest 20 entries could be reached.** `list_entries` accepted `cursor` and ignored it, and the diary page never paged. The API now does keyset pagination on (date, created, id) and the page has a "Load older entries" button. The ordering predicate is unit-tested; the query and the button are not. | `routers/v1/entries.py`, `apps/web/.../diaries/[diaryId]/page.tsx` | Fixed, tested |
| C-8 | **API error reasons never reached the UI.** The client read `json.detail`, but plain errors are sent as `{error: {message}}`, so users saw "HTTP 409" or "unauthorized". Login with a wrong password also triggered a pointless session refresh. Fixed, with readable messages on login and register. | `apps/web/src/lib/api.ts`, `authErrors.ts` | Fixed, tested |
| C-9 | **Weather labels were wrong.** WMO codes 71/73/75 were labelled moderate/heavy/"snowfall" instead of slight/moderate/heavy, and seven codes were missing. These labels are given to the model as facts. Source: <https://open-meteo.com/en/docs> | `workers/open_meteo.py` | Fixed, tested |
| C-10 | **Unvalidated input.** Any string was accepted as a timezone (a bad one fails every scan for that diary), relation, voice or scan interval; reversed entry dates and a null `entry_date` were accepted and surfaced as 500s. | `routers/v1/diaries.py`, `routers/v1/entries.py` | Fixed, tested |
| C-11 | **Backfill bookkeeping.** Cancelling a run was overwritten by "running" or "completed" (fixed, tested). Fixed 2026-11-14 (TR-1): chunks no longer overlap; the backfill runs ingest and evaluation in-process, so `entries_created` counts only entries created during the run; a run that cannot get the scan lock waits and retries (60 s, 5 times), then fails with a message; an event that cannot be saved is skipped and counted in a note; the per-chunk weather step was removed (drafting adds weather). Scan runs now record `triggered_by` ("manual" from the API) and `entries_created`. **Still partly open:** a scan's freshly fetched events are evaluated later by Celery, so a scan's `entries_created` counts only events that were already due. The web therefore shows "N events checked" for a scan run with no entries of its own, not "0 new entries". | `workers/tasks.py`, `workers/backfill.py` | Partly fixed (tests/integration/test_backfill_worker.py, test_scan_counts.py) |
| C-12 | **Two rules matching one event create a second, empty entry** that counts toward the tier limit and gets a failed "no inputs" generation. `test_two_rules_matching_same_event` asserts this, but the original plan (`docs/superpowers/plans/2026-05-25-calendar-entry-p3-rules-engine.md:585`) intended a shared entry, so the test encodes the bug. Decided 2026-11-11: one entry per event, oldest rule decides grouping. | `workers/rules.py` | Fixed, tested (25.1) |
| C-13 | **All-day events have no `occurred_at`**, so they are excluded from rule preview and "apply to past N days", and sort to the top of the event picker regardless of date. | `workers/tasks.py`, `routers/v1/calendar_events.py`, `routers/v1/rules.py` | Fixed, tested (25.1) |
| C-14 | **Account restore is only reachable for 15 minutes.** Deletion revokes refresh tokens and login refuses deleted users, so restore works only while the last access token lives. The design's restore-link email is not built. | `routers/v1/auth.py` | Open |
| C-15 | **Diary settings could not clear a field.** PATCH dropped every null except `lat`/`lon`, so voice could not be reset to automatic and the subject name could not be removed; the UI reported success. Nulls are now applied for the nullable columns. | `routers/v1/diaries.py` | Fixed, tested |
| C-16 | (1) "Scan options → past N days" had no effect once a sync token existed: fixed (TR-4); a past or future window is now fetched on its own, without a sync token, and the stored cursor is left alone. (2) The single stored cursor would be reused across calendars if calendar filters are ever written (nothing writes them today): later (TR-5). (3) The default calendar-only connect always redirected with `google=partial&missing=photos`: fixed by item 14 (TR-6). | `workers/calendar_sync.py`, `routers/v1/integrations.py` | Parts 1 and 3 fixed (tests/integration/test_calendar_sync_window.py); part 2 later |
| C-17 | The "verify these words" warning is noisy: it flags the subject's own name and any capitalised sentence-opening word, which trains users to ignore it. Fixed (TR-7): the check is case-insensitive and whole-word, counts the subject's name as known, and skips a fixed list of sentence openers (number words are not on it, so an invented count is flagged). It reads cited events as the prompt's event line, not the raw JSON, so attendee emails and JSON keys support no word and a name after an escaped newline is found. | `workers/llm.py` `validate_citation` | Fixed, tested (tests/unit/test_llm_text_rules.py) |
| C-18 | **A lost Google connection is silent.** When Google answers `invalid_grant`, `ensure_fresh_access_token` marks the token revoked and every later scan skips the calendar but records `success` with 0 events and no error. The diary page only swaps in a Connect button. CLAUDE.md: failed scans must not be silently skipped. Found in the 2026-11-12 walkthrough (the grant had lapsed since June). | `workers/tasks.py`, `workers/token_refresh.py`, diary page | Fixed, tested (`test_calendar_connection.py`, `calendar-connection.spec.ts`). Scan errors now reach the page too (`ScanRunOut` had dropped them). |
| C-19 | **Declining the calendar permission reads as "cancelled or failed".** When Google grants only the identity scopes, `granted_scopes` is empty and the callback redirects with `google=denied`, so the page shows the generic failure text instead of "calendar access was not granted". The same attempt overwrites the stored token with an empty scope list. Found 2026-11-12. | `routers/v1/integrations.py` | Fixed, tested (`TestCallbackAnswers`). Also fixed: connecting with calendar only reported "Photos access was not granted". |
| C-20 | **Switched-off buttons looked clickable.** No style for `button:disabled`, so Regenerate on a published entry (disabled since 25.3) looked like it ignored the click; the reason was only in a hover tooltip. Found 2026-11-12. | `globals.css`, entry page | Fixed, tested (browser test) |
| C-21 | **A viewer can restore a deleted entry.** `POST /v1/entries/{id}/restore` looks the diary up with `_get_diary_or_404` and never checks the caller's role, so anyone with view access can bring an entry back from the trash. Found 2026-11-12 while gating suggestions to editors. | `routers/v1/entries.py` (`restore_entry`) | Fixed, tested (`TestRestoreEntryPermissions`) |

## LLM integration

| ID | Finding | Where | Status |
|---|---|---|---|
| L-1 | The model is hard-coded to `claude-haiku-4-5-20251001`. The design doc and `CLAUDE.md` call for Sonnet or Opus. Make it a setting and choose deliberately. | `workers/llm_providers.py` | Fixed, tested (25.2) |
| L-2 | Prompt caching is configured on a ~30-token block, far below the minimum cacheable size, so nothing is cached. The cost estimate in `04-llm-integration.md` assumes a 70% hit rate. | `workers/llm_providers.py` | Open |
| L-3 | The synchronous Anthropic client is called inside an `async` function. `max_tokens=1024` with no `stop_reason` check means a long multi-event entry is cut mid-JSON and falls to the fallback list. Invalid JSON skips straight to the next provider instead of the one retry the design specifies. | `workers/llm_providers.py`, `workers/llm.py` | Fixed, tested (25.2) |
| L-4 | The system prompt says "use only facts in EVENTS" and "do not invent weather", then supplies weather under "ENRICHMENTS (use if helpful)". The model has no way to cite an enrichment. | `workers/llm.py` | Closed, won't fix (TR-15): reworded in 25.2; the validator already counts enrichments as a source. Checked with two live drafts, no automated test |
| L-5 | Attendee email addresses are sent to the model when no display name exists. Fixed (TR-8): nameless attendees become "and N other(s)" or "N people"; no emails reach the prompt. | `workers/llm.py` `_format_event_line` | Fixed, tested (tests/unit/test_llm_text_rules.py) |
| L-6 | Regenerate has no rate limit and no tier check; item 18's "5 AI drafts per month" is not enforced anywhere. | `routers/v1/entries.py` | Open (item 18) |

## Deployment

None of this was verified on the NUC.

| ID | Finding | Where | Status |
|---|---|---|---|
| P-1 | **The production web bundle called `http://localhost:8000`.** `NEXT_PUBLIC_API_URL` was only set as a runtime variable, but Next.js inlines `NEXT_PUBLIC_*` at build time and the Dockerfile declared no build argument. Added build arguments to the Dockerfile, compose and the deploy workflow. No image was built to confirm. | `apps/web/Dockerfile`, `docker-compose.yml`, `deploy.yml` | Fixed, checked by building the image |
| P-2 | **`40-update.sh` does not deploy new code.** Compose defines the app services with `build:` and no `image:`, so `pull` fetches nothing and `up -d` reuses the image from the first deploy. Migrations run from the old image too. `50-rollback.sh` retags GHCR images compose never references. It also pulls from `ghcr.io/andrewlass/...` while the repo owner is `bdsys`. The design intends GHCR images; the compose file was never updated to match. | `scripts/nuc/40-update.sh`, `50-rollback.sh`, `docker-compose.yml` | Open |
| P-3 | `deploy.yml` failed on every push to `main` with a workflow-file error (0 s runtime). Cause: a step used `if: secrets.SENTRY_AUTH_TOKEN != ''`, and the `secrets` context is not allowed in step conditions. Moved the secret into the job `env` and test that. Confirmed only by YAML parse; the next push to `main` will show whether it was the only error. | `.github/workflows/deploy.yml` | Fixed, checked with actionlint |
| P-4 | `app.env` was written mode 600 `root:docker`, so the docker group (and the `perfectday` service user) could not read it. Now 640. | `scripts/nuc/10-secrets.sh` | Fixed, untested |
| P-5 | No lockfiles are committed (`package-lock.json` and `pnpm-lock.yaml` are git-ignored; Python deps are `>=` ranges installed with `pip install -e .`). Every image build can pull different versions. 15 Dependabot PRs have been open since May. **This bit on 2026-11-11:** SQLAlchemy 2.1 was pulled in by CI and by image builds; under it a bare `postgresql://` URL selects psycopg 3, which is not installed, so `alembic upgrade head` failed in every freshly built API image. Pinned `<2.1` as a stopgap. | `.gitignore`, `apps/api/Dockerfile`, `apps/web/Dockerfile` | Fixed 2026-11-12: `requirements.lock` / `requirements-dev.lock` with hashes (`make lock`), installed by the API image and CI; `package-lock.json` committed and installed with `npm ci`. The web lock had been git-ignored, so the web app was never locked. The SQLAlchemy `<2.1` cap stays until its own change. |
| P-6 | The API container runs as root. `minio/minio:latest` and other `latest` tags are unpinned. | `apps/api/Dockerfile`, `docker-compose.yml` | Open |
| P-7 | **The MinIO images no longer exist upstream.** `minio/minio` and `minio/mc` have been removed from Docker Hub and are not pullable anonymously from quay.io (found 2026-11-11 when CI could not pull them). Dev machines and the NUC only work because they hold a cached copy (`RELEASE.2025-09-07`); a fresh machine, or the NUC after an image prune, cannot start object storage. The integration tests now use `cgr.dev/chainguard/minio:latest`. `docker-compose.yml`, the `Makefile` and `scripts/seed-minio-bucket.sh` still name the removed images: replacing them is a production decision (the Chainguard image runs as a non-root user, so the existing data volume's ownership would need changing). | `docker-compose.yml`, `Makefile`, `scripts/seed-minio-bucket.sh`, `tests/integration/conftest.py` | Tests fixed; production open |
| P-12 | **Local tests run on a different Python and different library versions from production.** Bootstrap makes `apps/api/.venv` with the machine's `python3` (3.14 on Andrew's PC) and installs without the lock, so `make test-all`'s API tests do not run what the image and CI run (Python 3.12, locked versions). Found 2026-11-12. Fixed (TR-39): `scripts/bootstrap-local.sh` now builds `apps/api/.venv` with `uv`, Python 3.12, from `requirements-dev.lock`. | `scripts/bootstrap-local.sh` | Fixed |

## Design drift

- **Tiers.** Code uses `free / tier1 / tier2` with Free capped at 5 manual and 3 auto entries for the lifetime of the account. `COMPETITION.md` says Free / Plus / Family, unlimited manual entries, 5 AI drafts per month. Photo uploads are not gated. (Item 18.)
- **Google Photos.** `photoslibrary.readonly` was removed by Google on 2025-03-31; item 14 needs a redesign around the Picker API. Source: <https://developers.google.com/photos/support/updates>
- **Edit diffs.** `entry_edit_diffs` is never written. (`publish_entry` used to run a query and discard the result; that dead query is removed.)
- **Markdown.** `body_markdown` is shown as plain text, so the fallback body's `**bold**` markers appeared literally. Fixed (TR-9): the fallback body has no Markdown, the prompt says "Do not use Markdown", and paired `**`/`__` and leading `#` heading markers are stripped from the model's title and body before they are stored. The sanitising render pipeline in `03-api-surface.md` is not built. (No XSS exposure as a result.)
- **Scan retries.** `scan_diary` declares `max_retries=3` but never retried. Fixed (TR-2, TR-25): a transient calendar failure now increments `consecutive_failures` and sets `next_scan_after` by `scan_backoff_seconds` (60 s doubling, capped at the scan interval); a disconnected calendar does not count. Retries happen at the 5-minute dispatch tick. The location tasks retry with `self.retry`. Test: tests/integration/test_scan_retry.py.

## Decisions made (2026-11-11)

All six questions were answered by Andrew on 2026-11-11. The designs, with the calls made while writing them up, are in [`docs/superpowers/specs/2026-11-11-code-review-decisions-design.md`](../docs/superpowers/specs/2026-11-11-code-review-decisions-design.md). Nothing below is built yet.

1. **C-6:** Future events are still read, but an entry is only created once the event has ended. Cancellations and reschedules apply to anything not yet published.
2. **C-5:** Replaced the proposed `body_source = 'user'` marker alone with a suggestion box: when an entry has text, the AI's version appears beside it with Accept, and never overwrites. The first draft of an empty entry still goes straight in. The marker is still added, to tell machine-only text from the parent's.
3. **C-12:** One entry per event; every matching rule is recorded; the oldest rule decides grouping. This matches the original plan, so it is a bug fix, not a change of intent.
4. **L-1:** `claude-opus-5-5`, as a setting, with a weekly check that warns when the model is gone or a newer Opus exists.
5. **P-2:** CI builds and pushes images; a timer on the NUC pulls and deploys them. No SSH from GitHub into the house.
6. **S-1 follow-up:** Only test photos exist. Delete them and remove the code that reads the old format; no re-encryption script.

New backlog item from the C-12 discussion: automatic entries with no rule-writing. See the same document.

## Decisions made (2026-11-12)

Made by Andrew while fixing C-18 and C-19.

1. **C-18, never connected:** a diary whose owner never connected Google scans quietly, with no warning. On the diary page, Scan now, the scan options and Backfill are grayed out, with visible text (and a tooltip) saying to connect Google Calendar. A connection that exists but cannot be read is always reported, as a scan error and a banner.
2. **Scanning stays owner-only, to keep the owner's calendar private.** Scans read the diary owner's Google Calendar, so only the owner can start a scan or a backfill. Co-parents (editors and viewers) see those buttons grayed out with "Only the diary owner can scan for new events." If the owner's connection is lost, they see a banner asking them to tell the owner to reconnect, with no Reconnect button of their own. **To revisit:** the checks are `require_owner=True` in `apps/api/app/routers/v1/scan.py` (`trigger_scan`, `trigger_backfill`, `cancel_backfill_run`), and the diary page's owner check.
3. **S-11: the synced calendar is the owner's alone.** Only the owner can list the diary's synced calendar events or make an entry from one ("New entry from Google Calendar"). Co-parents, editors included, don't see that button; they can still write entries by hand and edit drafts. Entries keep showing the events they were made from, as before. **To revisit:** `require_owner=True` in `list_calendar_events` and `create_entry_from_event` in `apps/api/app/routers/v1/calendar_events.py`, and the `isOwner` check on the diary page.
4. **S-13: event addresses and attendees are the owner's alone.** Co-parents and viewers see each event's title, time and description, and the entry's town from its place line, but not the event's location text or its attendees. Descriptions stay visible for now (Andrew, 2026-11-12). **To revisit:** `exact_places` in `_event_out_from_orm` in `apps/api/app/routers/v1/entries.py`.
5. **S-14: auto-creation rules are the owner's alone.** Rules run over the owner's synced calendar, and the rule preview searches all of it by title, location and attendee email. Only the owner can list, create, change, delete, preview or apply rules; co-parents, editors included, get 403, and the diary page shows them no rules button or rules link. Entries a rule already made stay visible to co-parents as before (Andrew, 2026-11-12). **To revisit:** `require_owner=True` in `apps/api/app/routers/v1/rules.py`, and the `isOwner` checks on the diary page and the 403 screen on the rules page.

## Triage (2026-11-14)

Andrew went through every finding still open, plus the minors deferred by items 24, 25.3 and 14, and marked each one **fix**, **later** or **won't fix**. Each row was checked against the code on 2026-11-14, except the item 24 / 25.3 minors (TR-19 to TR-28), which are as logged on 2026-11-12/13. The status cells in the tables above are updated as each fix lands. Until then, this section is the record.

The item 24 and 25.3 minors were listed only in a git-ignored build folder that had been deleted. They were recovered from the session transcript. From now on, deferred findings go in full into the PR body or a committed doc.

**Where the fixes go.** Items marked ⓐ are on `main` today, so they go into one "review fixes" plan off `main`. Items marked ⓑ change item 14 code (PR #51, which is not merged yet), so they are built on #51's branch before it merges.

### Review findings and design drift

| # | Finding | Outcome | Note |
|---|---|---|---|
| TR-1 | C-11 backfill bookkeeping | **Fix** ⓐ | Task id used as entry id (no per-chunk weather, wrong counts); a run that can't get the lock says "completed"; overlapping chunks; scan runs report 0 entries and `beat`. **Built** on fix/review-triage-2026-11-14. Scan counts only partly: see C-11. |
| TR-2 | Scan retries (design drift) | **Fix** ⓐ | `scan_diary` declares 3 retries but never retries; a calendar failure resets the failure counter. Includes TR-25. **Built** on fix/review-triage-2026-11-14. |
| TR-3 | C-14 restore only reachable for 15 minutes | Later: PoC item 29 (account settings) | The web app has no "delete account" control yet, so no parent can reach this. The documented design restores by emailed link (`08-security-privacy.md`); item 29 decides login, link or both (Andrew, 2026-11-14). |
| TR-4 | C-16a "past N days" ignored once a sync token exists | **Fix** ⓐ | One-off windowed fetch that leaves the sync token alone. **Built** on fix/review-triage-2026-11-14. |
| TR-5 | C-16b one cursor across calendars | Later | Only matters once calendars can be chosen. |
| TR-6 | C-16c calendar-only connect said Photos missing | Fixed already | Item 14: `integrations.py` compares against the scopes asked for. |
| TR-7 | C-17 noisy "verify these words" | **Fix** ⓐ | Count the subject's name as known; compare ignoring case. **Built** on fix/review-triage-2026-11-14. |
| TR-8 | L-5 attendee emails sent to the model | **Fix** ⓐ | Leave nameless attendees out (or "and N others"). **Built** on fix/review-triage-2026-11-14. |
| TR-9 | Markdown shown literally (design drift) | **Fix** ⓐ | The fallback body stops producing Markdown; Markdown markers are stripped from model text before it is stored. **Built** on fix/review-triage-2026-11-14. |
| TR-10 | Account deletion doesn't revoke Google access | **Fix** ⓑ | Reuse item 14's disconnect-revoke (S-8). |
| TR-11 | Google's one-time OAuth code in API access logs | Later (NUC logging) | Single-use, and needs the client secret. |
| TR-12 | S-9 diary-limit lock untested | Later (item 18) | Only matters with more than one API process. |
| TR-13 | S-10 refresh accepts a missing `Origin` | Won't fix | The refresh cookie is `SameSite=Strict` (`routers/v1/auth.py` `_set_refresh_cookie`), so a cross-site request never carries it. |
| TR-14 | L-2 prompt caching never caches | Later (item 18) | Correct the 70% hit-rate assumption in `04-llm-integration.md` when item 18's cost work starts. |
| TR-15 | L-4 enrichments | Won't fix (closed) | Reworded in 25.2. The validator already counts enrichments as a source. |
| TR-16 | Edit diffs never written (design drift) | Later | Nothing reads them yet. |
| TR-17 | Tiers (design drift) | Later (item 18) | |
| TR-18 | Google Photos library scope (design drift) | Resolved by item 14 | Close when #51 merges. |

P-2, P-4, P-6, P-7, D-5, D-6 and S-6 stay with the NUC deployment work. L-6 is item 18.

### Item 24 and 25.3 deferred minors

| # | Minor | Outcome |
|---|---|---|
| TR-19 | `/v1/geocode` waits up to 30 s on Open-Meteo; no loading indicator in the place search | **Fix** ⓐ: shorter timeout and a spinner  Built on fix/review-triage-2026-11-14. |
| TR-20 | `DiaryOut` lat/lon (home coordinates) returned to co-parents and viewers | **Fix** ⓐ: owner-only, like S-12 (Andrew, 2026-11-14)  Built on fix/review-triage-2026-11-14. |
| TR-21 | A PATCH setting both `entry_date` and `tz_override` recomputes the date over the hand-set one | **Fix** ⓐ  Built on fix/review-triage-2026-11-14. |
| TR-22 | An event joining an existing rule-claimed entry doesn't refresh the entry's place | **Fix** ⓐ  Built on fix/review-triage-2026-11-14. |
| TR-23 | Cached place JSON rebuilt with `Place(**json)` would raise if a required field is added | Won't fix now: handle it when a field is added |
| TR-24 | A home place with no name is labelled "From a photo" | **Fix** ⓐ  Built on fix/review-triage-2026-11-14. |
| TR-25 | `refresh_entry_location` / `refresh_diary_locations` never retry | **Fix** ⓐ, with TR-2  Built on fix/review-triage-2026-11-14. |
| TR-26 | The time-zone label uses today's date (PST shown for a summer entry) | **Fix** ⓐ  Built on fix/review-triage-2026-11-14. |
| TR-27 | An unknown time zone gives Pydantic's generic 422, not `unknown_time_zone` | Later: the UI only offers a picker |
| TR-28 | 25.3: a background redraft can leave an older pending suggestion | Later: the parent can dismiss it |

### Item 14 (PR #51) deferred minors

The full list is in PR #51's body. Grouped:

| # | Theme | Fix now | Later |
|---|---|---|---|
| TR-29 | **Refresh-token race** (older than item 14; parallel uploads make it likelier). Refresh doesn't lock the token row, so two refreshes at once can leave two live tokens in one family; a later grace-window lookup expects one (`scalar_one_or_none`) and errors. Not reproduced yet. | **Fix** ⓐ: test with two refreshes at once first; lock the row; the web client shares one refresh. **Server half fixed** on fix/review-triage-2026-11-14 (tests/integration/test_refresh_race.py); client half is in Plan B, on PR #51's branch | |
| TR-30 | Non-uploaders' JPEGs keep non-GPS EXIF, including the MakerNote and possibly an embedded thumbnail (not known whether either can carry location) | **Fix** ⓑ: non-uploaders keep only orientation | |
| TR-31 | Phone uploads | **Fix** ⓑ: upload timeout; no duplicate photo when attach fails after upload | Cancel uploads on Edit |
| TR-32 | Google import worker | **Fix** ⓑ: guarded status writes; bearer token only to Google's photo host; missing field in Google's reply → handled error, not 500; edit rights re-checked per item | Concurrent-pass lock, orphaned encrypted objects, memory copies, page caps, per-user cap on waiting imports |
| TR-33 | Google connect | **Fix** ⓑ: non-ASCII cookie → 500; scan/backfill after disconnect (`scalar_one` → stuck "running"); double-tap guard on the diary page's Calendar connect | `return_to` fragment and length cap; "lost" → "none" wording; Back-button cache |
| TR-34 | Entry page | **Fix** ⓑ: import finishing during Edit leaves the strip stale; poll errors never end waiting; failed list shows "Connect"; "Google Photos is open" during copying; `opener` on the picker tab | Accessible names for repeated buttons, `aria-live` |
| TR-35 | Photo storage | **Fix** ⓑ: display backfill retries bad photos first; display-copy failure not logged; index on `entry_photos(photo_id)` | One location refresh per batch; stored location-free copy; memory; orphaned objects |
| TR-36 | `03-api-surface.md` says photos on a deleted diary are unreadable even to the owner | **Fix** ⓑ (doc): a photo belongs to the uploader's library, so the uploader can read it | |
| TR-37 | Missing tests listed per task; misnamed helper; stale docstring; long lines; old item-13 row | Won't fix as a batch: add a test when that code is next touched | |
| TR-38 | `S3_PUBLIC_ENDPOINT_URL` and the `media.` upload route now unused; httpcore detail in debug logs | NUC work | |

Noted, not bugs: the sweep ends a killed import sooner than Celery's one-hour redelivery would, and it leaves Google's picker session to expire on its own.

### Open decisions settled

| # | Decision | Outcome |
|---|---|---|
| TR-39 | P-12: local tests on Python 3.14 with unlocked versions; the image and CI use 3.12 and the lock | **Fix** ⓐ: `scripts/bootstrap-local.sh` builds `.venv` with Python 3.12 (via `uv`) from `requirements-dev.lock`  Built on fix/review-triage-2026-11-14. |
| TR-40 | `__Host-` prefix for the Google-connect cookie (item 14 Task 6) | **Fix** ⓑ: `__Host-pd_google_connect`, path `/`. Stops a sibling `*.andrewlass.com` site planting a connect cookie, which would defeat S-5. |
| TR-41 | Open-Meteo licence (free API is non-commercial) | Later: decide in item 18, before any paid tier ships; re-check their terms then |
