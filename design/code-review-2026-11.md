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
| S-10 | `/readyz` returned raw exception text to anonymous callers (now logs it and returns `error`; tested). `require_reauth` called `run_until_complete` inside a running loop (now an async dependency; tested). `python-jose` floor raised from 3.3 to 3.4, the release that fixes CVE-2024-33663/33664. Still open: the refresh `Origin` check accepts a missing header. | `routers/health.py`, `core/auth.py`, `pyproject.toml` | Mostly fixed |

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

## Correctness

| ID | Finding | Where | Status |
|---|---|---|---|
| C-1 | **Calendar sync stopped permanently after a sync token expired.** On HTTP 410 the dead token was never cleared, so every later scan got 410 again and reported success with zero events. Now falls back to a full sync and stores the fresh token. | `workers/calendar_sync.py` | Fixed, tested |
| C-2 | **A calendar that kept answering 429 looped forever** while the heartbeat kept the scan lock alive. Waits are capped; `Retry-After` given as a date no longer crashes. | `workers/calendar_sync.py`, `workers/backfill.py` | Fixed, tested |
| C-3 | **Any non-200 from Google's token endpoint permanently disconnected the integration**, including 5xx and 429. Only `invalid_grant` revokes now. | `workers/token_refresh.py` | Fixed, tested |
| C-4 | **Background jobs were queued before their rows were committed.** A worker that picked the task up first found nothing and returned silently: drafts never generated, backfill runs stuck "pending". Fixed in the rules engine, the backfill endpoint and create-from-event. `trigger_scan` has the same shape (the worker can read the old `next_scan_after` and skip) and is still open. | `workers/rules.py`, `routers/v1/scan.py`, `routers/v1/calendar_events.py` | Fixed, tested |
| C-5 | **Regenerating treats the previous AI draft as the diarist's own writing.** Mode is chosen from "is the body non-empty", ignoring who wrote it. Regenerating an AI or fallback draft therefore runs in hybrid mode, which tells the model the old draft is "the diarist's lived experience", skips the token check, and clears the "verify before publishing" warning. Any fabrication in the first draft becomes permanent. Fix needs a migration: add `user` to `entries.body_source`, set it when a person writes or edits the body, and pick `events` mode for machine-written bodies. | `workers/llm.py`, `routers/v1/entries.py` | Open |
| C-6 | **Entries are drafted for events that have not happened.** Scans ingest 90 days ahead and rules create and draft entries for those events immediately. A diary can narrate a soccer game that is later cancelled. Also, once an event is ingested, a later cancellation or reschedule is never applied (`occurred_at` is not updated; cancelled events are skipped rather than removed). Needs a decision on intended behaviour. | `workers/calendar_sync.py`, `workers/tasks.py`, `workers/rules.py` | Fixed, tested (25.1) |
| C-7 | **Only the newest 20 entries could be reached.** `list_entries` accepted `cursor` and ignored it, and the diary page never paged. The API now does keyset pagination on (date, created, id) and the page has a "Load older entries" button. The ordering predicate is unit-tested; the query and the button are not. | `routers/v1/entries.py`, `apps/web/.../diaries/[diaryId]/page.tsx` | Fixed, tested |
| C-8 | **API error reasons never reached the UI.** The client read `json.detail`, but plain errors are sent as `{error: {message}}`, so users saw "HTTP 409" or "unauthorized". Login with a wrong password also triggered a pointless session refresh. Fixed, with readable messages on login and register. | `apps/web/src/lib/api.ts`, `authErrors.ts` | Fixed, tested |
| C-9 | **Weather labels were wrong.** WMO codes 71/73/75 were labelled moderate/heavy/"snowfall" instead of slight/moderate/heavy, and seven codes were missing. These labels are given to the model as facts. Source: <https://open-meteo.com/en/docs> | `workers/open_meteo.py` | Fixed, tested |
| C-10 | **Unvalidated input.** Any string was accepted as a timezone (a bad one fails every scan for that diary), relation, voice or scan interval; reversed entry dates and a null `entry_date` were accepted and surfaced as 500s. | `routers/v1/diaries.py`, `routers/v1/entries.py` | Fixed, tested |
| C-11 | **Backfill bookkeeping.** Cancelling a run was overwritten by "running" or "completed" (fixed, tested). Still open: the Celery task id is used as an entry id, so per-chunk weather enrichment does nothing and `entries_created` counts events; an integration test hides this by making `.delay()` return an entry id. Weekly chunks overlap by a day. A run that cannot get the scan lock is marked "completed" with zero events. Scan runs always report `entries_created = 0` and `triggered_by = "beat"`. | `workers/tasks.py`, `workers/backfill.py` | Partly fixed |
| C-12 | **Two rules matching one event create a second, empty entry** that counts toward the tier limit and gets a failed "no inputs" generation. `test_two_rules_matching_same_event` asserts this, but the original plan (`docs/superpowers/plans/2026-05-25-calendar-entry-p3-rules-engine.md:585`) intended a shared entry, so the test encodes the bug. Decided 2026-11-11: one entry per event, oldest rule decides grouping. | `workers/rules.py` | Fixed, tested (25.1) |
| C-13 | **All-day events have no `occurred_at`**, so they are excluded from rule preview and "apply to past N days", and sort to the top of the event picker regardless of date. | `workers/tasks.py`, `routers/v1/calendar_events.py`, `routers/v1/rules.py` | Fixed, tested (25.1) |
| C-14 | **Account restore is only reachable for 15 minutes.** Deletion revokes refresh tokens and login refuses deleted users, so restore works only while the last access token lives. The design's restore-link email is not built. | `routers/v1/auth.py` | Open |
| C-15 | **Diary settings could not clear a field.** PATCH dropped every null except `lat`/`lon`, so voice could not be reset to automatic and the subject name could not be removed; the UI reported success. Nulls are now applied for the nullable columns. | `routers/v1/diaries.py` | Fixed, tested |
| C-16 | "Scan options → past N days" has no effect once a sync token exists (the token path cannot use a time window). The single stored cursor would also be reused across calendars if calendar filters are ever written (nothing writes them today). The default calendar-only connect always redirects with `google=partial&missing=photos`. | `workers/calendar_sync.py`, `routers/v1/integrations.py` | Open |
| C-17 | The "verify these words" warning is noisy: it flags the subject's own name and any capitalised sentence-opening word, which trains users to ignore it. | `workers/llm.py` `validate_citation` | Open |

## LLM integration

| ID | Finding | Where | Status |
|---|---|---|---|
| L-1 | The model is hard-coded to `claude-haiku-4-5-20251001`. The design doc and `CLAUDE.md` call for Sonnet or Opus. Make it a setting and choose deliberately. | `workers/llm_providers.py` | Open |
| L-2 | Prompt caching is configured on a ~30-token block, far below the minimum cacheable size, so nothing is cached. The cost estimate in `04-llm-integration.md` assumes a 70% hit rate. | `workers/llm_providers.py` | Open |
| L-3 | The synchronous Anthropic client is called inside an `async` function. `max_tokens=1024` with no `stop_reason` check means a long multi-event entry is cut mid-JSON and falls to the fallback list. Invalid JSON skips straight to the next provider instead of the one retry the design specifies. | `workers/llm_providers.py`, `workers/llm.py` | Open |
| L-4 | The system prompt says "use only facts in EVENTS" and "do not invent weather", then supplies weather under "ENRICHMENTS (use if helpful)". The model has no way to cite an enrichment. | `workers/llm.py` | Open |
| L-5 | Attendee email addresses are sent to the model when no display name exists. | `workers/llm.py` `_format_event_line` | Open |
| L-6 | Regenerate has no rate limit and no tier check; item 18's "5 AI drafts per month" is not enforced anywhere. | `routers/v1/entries.py` | Open (item 18) |

## Deployment

None of this was verified on the NUC.

| ID | Finding | Where | Status |
|---|---|---|---|
| P-1 | **The production web bundle called `http://localhost:8000`.** `NEXT_PUBLIC_API_URL` was only set as a runtime variable, but Next.js inlines `NEXT_PUBLIC_*` at build time and the Dockerfile declared no build argument. Added build arguments to the Dockerfile, compose and the deploy workflow. No image was built to confirm. | `apps/web/Dockerfile`, `docker-compose.yml`, `deploy.yml` | Fixed, checked by building the image |
| P-2 | **`40-update.sh` does not deploy new code.** Compose defines the app services with `build:` and no `image:`, so `pull` fetches nothing and `up -d` reuses the image from the first deploy. Migrations run from the old image too. `50-rollback.sh` retags GHCR images compose never references. It also pulls from `ghcr.io/andrewlass/...` while the repo owner is `bdsys`. The design intends GHCR images; the compose file was never updated to match. | `scripts/nuc/40-update.sh`, `50-rollback.sh`, `docker-compose.yml` | Open |
| P-3 | `deploy.yml` failed on every push to `main` with a workflow-file error (0 s runtime). Cause: a step used `if: secrets.SENTRY_AUTH_TOKEN != ''`, and the `secrets` context is not allowed in step conditions. Moved the secret into the job `env` and test that. Confirmed only by YAML parse; the next push to `main` will show whether it was the only error. | `.github/workflows/deploy.yml` | Fixed, checked with actionlint |
| P-4 | `app.env` was written mode 600 `root:docker`, so the docker group (and the `perfectday` service user) could not read it. Now 640. | `scripts/nuc/10-secrets.sh` | Fixed, untested |
| P-5 | No lockfiles are committed (`package-lock.json` and `pnpm-lock.yaml` are git-ignored; Python deps are `>=` ranges installed with `pip install -e .`). Every image build can pull different versions. 15 Dependabot PRs have been open since May. **This bit on 2026-11-11:** SQLAlchemy 2.1 was pulled in by CI and by image builds; under it a bare `postgresql://` URL selects psycopg 3, which is not installed, so `alembic upgrade head` failed in every freshly built API image. Pinned `<2.1` as a stopgap. | `.gitignore`, `apps/api/Dockerfile`, `apps/web/Dockerfile` | Open (SQLAlchemy pinned) |
| P-6 | The API container runs as root. `minio/minio:latest` and other `latest` tags are unpinned. | `apps/api/Dockerfile`, `docker-compose.yml` | Open |
| P-7 | **The MinIO images no longer exist upstream.** `minio/minio` and `minio/mc` have been removed from Docker Hub and are not pullable anonymously from quay.io (found 2026-11-11 when CI could not pull them). Dev machines and the NUC only work because they hold a cached copy (`RELEASE.2025-09-07`); a fresh machine, or the NUC after an image prune, cannot start object storage. The integration tests now use `cgr.dev/chainguard/minio:latest`. `docker-compose.yml`, the `Makefile` and `scripts/seed-minio-bucket.sh` still name the removed images: replacing them is a production decision (the Chainguard image runs as a non-root user, so the existing data volume's ownership would need changing). | `docker-compose.yml`, `Makefile`, `scripts/seed-minio-bucket.sh`, `tests/integration/conftest.py` | Tests fixed; production open |

## Design drift

- **Tiers.** Code uses `free / tier1 / tier2` with Free capped at 5 manual and 3 auto entries for the lifetime of the account. `COMPETITION.md` says Free / Plus / Family, unlimited manual entries, 5 AI drafts per month. Photo uploads are not gated. (Item 18.)
- **Google Photos.** `photoslibrary.readonly` was removed by Google on 2025-03-31; item 14 needs a redesign around the Picker API. Source: <https://developers.google.com/photos/support/updates>
- **Edit diffs.** `entry_edit_diffs` is never written. (`publish_entry` used to run a query and discard the result; that dead query is removed.)
- **Markdown.** `body_markdown` is shown as plain text, so the fallback body's `**bold**` markers appear literally. The sanitising render pipeline in `03-api-surface.md` is not built. (No XSS exposure as a result.)
- **Scan retries.** `scan_diary` declares `max_retries=3` but never retries; a calendar failure is recorded as "partial" and resets the failure counter, so backoff never applies to it.

## Decisions made (2026-11-11)

All six questions were answered by Andrew on 2026-11-11. The designs, with the calls made while writing them up, are in [`docs/superpowers/specs/2026-11-11-code-review-decisions-design.md`](../docs/superpowers/specs/2026-11-11-code-review-decisions-design.md). Nothing below is built yet.

1. **C-6:** Future events are still read, but an entry is only created once the event has ended. Cancellations and reschedules apply to anything not yet published.
2. **C-5:** Replaced the proposed `body_source = 'user'` marker alone with a suggestion box: when an entry has text, the AI's version appears beside it with Accept, and never overwrites. The first draft of an empty entry still goes straight in. The marker is still added, to tell machine-only text from the parent's.
3. **C-12:** One entry per event; every matching rule is recorded; the oldest rule decides grouping. This matches the original plan, so it is a bug fix, not a change of intent.
4. **L-1:** `claude-opus-5-5`, as a setting, with a weekly check that warns when the model is gone or a newer Opus exists.
5. **P-2:** CI builds and pushes images; a timer on the NUC pulls and deploys them. No SSH from GitHub into the house.
6. **S-1 follow-up:** Only test photos exist. Delete them and remove the code that reads the old format; no re-encryption script.

New backlog item from the C-12 discussion: automatic entries with no rule-writing. See the same document.
