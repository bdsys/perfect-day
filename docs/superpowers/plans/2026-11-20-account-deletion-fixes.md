# Account deletion fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deleting an account, or a diary through the admin CLI, removes everything and sends nothing more anywhere; deleted data leaves the backups within about 4 months.

**Architecture:** One deletion path (`apps/api/app/workers/hard_delete.py`) used by the hourly job and the admin CLI; storage errors raise so the hourly job retries; row locks on `users` order restore, hard delete, account delete and the Google callback; the draft worker checks the owner's `deletion_state`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async (asyncpg) and sync (CLI), boto3 S3 (Garage), Celery beat, click, Next.js/Jest, bash tests.

**Spec:** `docs/superpowers/specs/2026-11-20-account-deletion-fixes-design.md`

## Global Constraints

- One definition of account state: `deletion_state()` in `apps/api/app/core/auth.py` (`active`, `pending`, `blocked`).
- Never log tokens or exception text from Google calls (follow `app/services/google_revoke.py`).
- Restore refused copy is the existing `authErrors.account_unavailable`: "This account is no longer available."
- Backup retention: newest 30 nightly folders plus the earliest of each of the last 3 months (`PD_KEEP_MONTHS=3`).
- Tests: API `PYTHONPATH=. .venv/bin/pytest` from `apps/api`; web `npx jest` from `apps/web`; bash `bash scripts/nuc/tests/backup.test.sh`. Whole suite: `make test-all`.

## Review Focus

1. A hard delete interrupted half-way (storage down after the diaries are gone) is retried and finishes on the next hourly run; `test_user_scrub_failure_keeps_user_for_retry` (Task 1).
2. An admin `--hard` delete of an **active** account (never soft-deleted) still works; `test_cli_hard_delete_active_user_scrubs_storage` (Task 5).
3. A photo used by two diaries survives the CLI delete of one; `test_cli_hard_delete_diary_keeps_shared_photo` (Task 5).
4. A Google connect for an account deleted mid-exchange revokes the new grant even when Google returns only an access token (no refresh token); `test_callback_after_delete_revokes_access_only_token` (Task 4).
5. A restore that loses the race is told the account is gone, not "try again"; `RestoreAccountScreen` test (Task 2).

---

### Task 1: Storage errors stop a hard delete; hard delete revokes Google

**Files:**
- Modify: `apps/api/app/workers/hard_delete.py`
- Test: `apps/api/tests/integration/test_hard_delete_photos.py`

**Interfaces:**
- Produces: `hard_delete_diary(diary_id) -> None` and `hard_delete_user(user_id) -> None` raise on any storage error, leaving the rows in place (whole transaction rolled back).
- Consumes: `revoke_and_forget_google(db, token, *, user_id)` from `app/services/google_revoke.py`.

- [ ] **Step 1: Write the failing tests** in `test_hard_delete_photos.py`:
  - `test_user_scrub_failure_keeps_user_for_retry`: user with an avatar object; patch the S3 client's `delete_objects` to raise `ClientError`; `await hard_delete_user(id)` raises; the `users` row still exists; un-patch, run again: row gone, prefix empty.
  - `test_diary_photo_delete_failure_keeps_rows`: diary with one entry and one photo used only there; patch `delete_object` to raise; `hard_delete_diary` raises; `Photo` and `Diary` rows remain.
  - `test_hard_delete_user_revokes_google`: user with an `OAuthToken(provider="google")`; patch `httpx.AsyncClient.post` to return 200; after `hard_delete_user`, the revoke URL (`GOOGLE_REVOKE_URL`) was posted once and no token row remains.
- [ ] **Step 2: Run** `PYTHONPATH=. .venv/bin/pytest tests/integration/test_hard_delete_photos.py -v` — Expected: the three new tests FAIL (no raise; no revoke call).
- [ ] **Step 3: Implement.** Remove the `try/except … pass` and the outer `except` in `hard_delete_diary`'s photo loop and the `except` around the user prefix scrub; log `hard_delete_*_storage_failed` with ids and error type, then re-raise. In `hard_delete_user`, before deleting `OAuthToken` rows, `revoke_and_forget_google` each Google token.
- [ ] **Step 4: Run** the file — Expected: all PASS. Also `tests/integration/test_hard_delete.py` PASS.
- [ ] **Step 5: Commit** `fix(api): a storage error stops a hard delete so the hourly job retries; hard delete revokes Google`.

### Task 2: Restore and hard delete serialize on the user row (row 54)

**Files:**
- Modify: `apps/api/app/workers/hard_delete.py` (start of `hard_delete_user`), `apps/api/app/routers/v1/auth.py` (`restore_account`), `apps/web/src/components/RestoreAccountScreen.tsx`
- Test: `apps/api/tests/integration/test_account_grace.py`, `apps/web/src/components/__tests__/RestoreAccountScreen.test.tsx` (create if absent)

**Interfaces:**
- Produces: `hard_delete_user(user_id)` returns without deleting anything unless, under `SELECT … FOR UPDATE`, `deleted_at` is set and `hard_delete_after` is set and `<= now()`. Log `hard_delete_user_skipped_not_due`.
- Produces: `POST /v1/auth/account/restore` locks the row and answers 401 `account_unavailable` unless `deletion_state(row) in ("pending", "active")`.

- [ ] **Step 1: Failing tests.**
  - `test_hard_delete_stops_if_restored_after_pickup`: user with a diary, `deleted_at` and `hard_delete_after` cleared (as a restore leaves it); `await hard_delete_user(id)`; user and diary still exist.
  - `test_restore_refuses_when_row_no_longer_pending`: register, delete the account; override `get_current_user_within_grace` (via `app.dependency_overrides`) to return the user object while the DB row's `hard_delete_after` is 1 s in the past; POST restore → 401, detail `account_unavailable`; row still deleted.
  - Web: `restore()` rejecting `new ApiError(401, 'account_unavailable')` shows "This account is no longer available."; any other error still shows "Couldn't restore your account. Try again."
- [ ] **Step 2: Run** both — Expected: FAIL (hard delete deletes; restore returns 204; web shows the retry text).
- [ ] **Step 3: Implement** as in Interfaces. Web: check `e instanceof ApiError && e.status === 401 && e.message/detail === 'account_unavailable'` the way `authErrors.ts` callers read the code, and show `authErrors.account_unavailable`.
- [ ] **Step 4: Run** `tests/integration/test_account_grace.py tests/integration/test_hard_delete*.py` and the web test — Expected: PASS.
- [ ] **Step 5: Commit** `fix: restore and hard delete lock the account and re-check (row 54)`.

### Task 3: No AI drafts for an account being deleted (row 53)

**Files:**
- Modify: `apps/api/app/workers/llm.py` (`_generate_draft`, after the diary is loaded, before `is_diary_read_only` and any reservation)
- Test: `apps/api/tests/integration/test_review_fixes.py` (beside the existing draft-worker tests; reuse their LLM stub)

**Interfaces:**
- Produces: `_generate_draft` returns early when the diary owner is missing or `deletion_state(owner) != "active"`; log `generate_draft_skipped_owner_not_active`.

- [ ] **Step 1: Failing test** `test_draft_skipped_when_owner_deleted`: entry with an event; owner `deleted_at=now`, `hard_delete_after=now+7d`; `await generate_draft_for_entry(entry.id)`; the LLM stub was never called, no `ai_draft_spends` row, entry `body_markdown` unchanged. Same with an admin-blocked owner (`hard_delete_after=None`).
- [ ] **Step 2: Run** — Expected: FAIL (LLM called).
- [ ] **Step 3: Implement** (load owner once; reuse it for `temperature_unit`).
- [ ] **Step 4: Run** the file and `tests/integration/test_ai_draft_jobs_queue.py` — Expected: PASS.
- [ ] **Step 5: Commit** `fix(api): skip AI drafts for an account being deleted (row 53)`.

### Task 4: Google connect re-checks the account before saving (row 55)

**Files:**
- Modify: `apps/api/app/routers/v1/integrations.py` (`_google_callback`), `apps/api/app/routers/v1/auth.py` (`delete_account`), `apps/api/app/services/google_revoke.py`
- Test: `apps/api/tests/integration/test_account_grace.py`

**Interfaces:**
- Produces: `async def revoke_google_secret(secret: str, *, user_id: uuid.UUID) -> None` in `google_revoke.py` (posts to `GOOGLE_REVOKE_URL`, logs failures without the secret, never raises); `revoke_and_forget_google` uses it.
- Produces: the callback, after the exchange and before any save, runs `select(User).where(User.id == user_id).with_for_update()` and, if not `active`, calls `revoke_google_secret(refresh_token or access_token, …)` and redirects `google=failed, reason=account_unavailable`.
- Produces: `delete_account` begins with the same `with_for_update()` select of the user row.

- [ ] **Step 1: Failing tests.**
  - `test_callback_after_delete_revokes_new_grant`: authorize; patch `httpx.AsyncClient.post` with a side effect that, on `GOOGLE_TOKEN_URL`, marks the account deleted in the DB (committed, separate session) and returns tokens with `refresh_token="r1"`; on `GOOGLE_REVOKE_URL` returns 200. Expect redirect `account_unavailable`, 0 token rows, revoke posted with `token=r1`.
  - `test_callback_after_delete_revokes_access_only_token`: same, Google returns only `access_token="a1"`; revoke posted with `a1`.
  - `test_delete_account_locks_user_row`: capture SQL via a `before_cursor_execute` listener on the test engine; `DELETE /v1/auth/account`; a `SELECT … FROM users … FOR UPDATE` was issued before the `oauth_tokens` select.
- [ ] **Step 2: Run** — Expected: FAIL (token saved; no revoke; no FOR UPDATE).
- [ ] **Step 3: Implement** as in Interfaces (`populate_existing()` on the re-select so the session's cached `User` is refreshed).
- [ ] **Step 4: Run** `tests/integration/test_account_grace.py` and the integrations tests (`grep -l google/callback tests/integration`) — Expected: PASS.
- [ ] **Step 5: Commit** `fix(api): a Google connect that finishes after an account delete revokes the grant (row 55)`.

### Task 5: Admin CLI hard deletes reuse the app's deletion (row 51)

**Files:**
- Modify: `scripts/manage.py` (`users_delete`, `diaries_delete`, help texts), `scripts/ADMIN_CLI.md`
- Test: `apps/api/tests/integration/test_manage_cli.py`

**Interfaces:**
- Consumes: Task 1/2 `hard_delete_user`, `hard_delete_diary`.
- Produces: `--hard` first marks the row due in a committed step (`deleted_at = deleted_at or now`, `hard_delete_after = now`; for a diary also its entries' `deleted_at`), then runs the app function in one event loop (`asyncio.run`), disposing the async engine afterwards. If it raises, print that the deletion failed and that the hourly job will finish it, exit non-zero. `entries delete --hard` unchanged.

- [ ] **Step 1: Failing tests** (use the `s3_client`/`photos_bucket` fixtures and reset `app.core.database._engine`/`_session_factory` as `test_hard_delete_photos.py` does; answer the prompt with `input="yes\n"`):
  - `test_cli_hard_delete_active_user_scrubs_storage`: active user with an avatar object and a photo object; CLI `users delete --email … --hard`; exit 0; user row gone; `{user_id}/` prefix empty; Google revoke posted when a token exists.
  - `test_cli_hard_delete_diary_keeps_shared_photo`: photo A only in diary 1, photo B in diaries 1 and 2; `diaries delete --diary-id <1> --hard`; A's objects and row gone, B's remain.
  - `test_cli_hard_delete_entry_keeps_library_photo`: entry with a photo; `entries delete --hard`; photo row and object remain.
- [ ] **Step 2: Run** `tests/integration/test_manage_cli.py -v` — Expected: first two FAIL (objects remain), third PASS (pins current behaviour).
- [ ] **Step 3: Implement** as in Interfaces; update help and `ADMIN_CLI.md` ("removes stored photos too; if storage is down the hourly job finishes it").
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `fix(admin): --hard deletes clean storage the way the app does (row 51)`.

### Task 6: Backup retention 3 months; docs and tracker

**Files:**
- Modify: `scripts/nuc/lib/backup.sh:21-22,68-69`, `scripts/nuc/backup-run.sh:8`, `scripts/nuc/tests/backup.test.sh:104-109`, `deploy/nuc.md:80-81`, `design/08-security-privacy.md` § Backup, `POC_PHASE2_TODO.md` rows 51, 53, 54, 55 (row 51: "MinIO" → "storage")

- [ ] **Step 1: Failing test:** change the retention case to expect the newest 30 plus `2026-09-01` and `2026-10-01` (32 folders), wording "the first of each of the last 3 months (32 folders)".
- [ ] **Step 2: Run** `bash scripts/nuc/tests/backup.test.sh` — Expected: the retention checks FAIL.
- [ ] **Step 3: Implement** `PD_KEEP_MONTHS=3` and the comments; docs say deleted accounts leave the backups within about 4 months; tracker rows marked done with the PR number.
- [ ] **Step 4: Run** the bash test — Expected: all passed.
- [ ] **Step 5: Commit** `chore(nuc): keep 3 monthly backups, not 12, so deleted data leaves backups in ~4 months`.

### Finish

- [ ] `make test-all` in the main checkout, then the dev-stack check, then PR, `run-e2e` label, CI, merge (standing OK), deploy only with Andrew's OK. The deploy also applies the new retention: the next nightly run prunes monthly folders older than 3 months (none exist yet).
