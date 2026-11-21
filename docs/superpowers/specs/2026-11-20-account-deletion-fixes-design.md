# Account deletion that really deletes (rows 51, 53, 54, 55, and backup retention)

Approved by Andrew on 2026-11-20 ("B, and the design looks right, go ahead"). Short design: a small-to-medium change, one PR.

## Why

A parent who deletes their account, or asks for it to be deleted, is trusting that their child's photos and words are gone and that nothing more is sent anywhere. PR #69's review (2026-11-17) found four ways that isn't true. Reading the code on 2026-11-20 found two more in the same family, plus the backup question. CLAUDE.md requires: "Data deletion flow must cascade".

## Goals

1. **Row 51.** `scripts/manage.py users delete --hard` and `diaries delete --hard` run the app's own `hard_delete_user` / `hard_delete_diary` (`apps/api/app/workers/hard_delete.py`), so storage is scrubbed exactly as the app does it. `entries delete --hard` keeps today's behaviour (the entry's photos stay in the user's library, as the app does), now pinned by a test.
2. **Storage errors no longer skipped** (found 2026-11-20). `hard_delete_user` logs a storage failure and deletes the user row anyway, and `hard_delete_diary` swallows per-object delete errors and deletes the `Photo` rows anyway. Either way the objects stay in storage with nothing left to find them. Now a storage error raises: the transaction rolls back, the row stays due, and the hourly `process_hard_deletes` retries it (it already isolates one failing row from the rest).
3. **Row 53.** `generate_entry_draft` (`apps/api/app/workers/llm.py`) skips an entry whose diary owner is not `active` (`deletion_state()` in `app/core/auth.py`), before any LLM call or draft reservation.
4. **Row 54.** Restore and hard delete serialize on the user row:
   - `hard_delete_user` first locks the user row (`SELECT … FOR UPDATE`) and re-checks it is still due (`deleted_at` set, `hard_delete_after` set and passed); if not, it stops.
   - `POST /v1/auth/account/restore` locks the same row and re-checks `deletion_state` is `pending`; otherwise it answers 401 `account_unavailable`.
   - The web restore screen shows the existing copy for that code, "This account is no longer available.", instead of "Couldn't restore your account. Try again."
5. **Row 55.** The Google callback (`app/routers/v1/integrations.py`) re-selects the user `FOR UPDATE` after the code exchange and re-checks `deletion_state == "active"` before saving. If the account was deleted meanwhile, it revokes the token Google just issued and saves nothing (redirect `reason=account_unavailable`). `DELETE /v1/auth/account` takes the same row lock before it looks for the Google token, so either the delete sees the saved token and revokes it, or the callback sees the deleted account.
6. **Hard delete revokes Google** (found 2026-11-20). `hard_delete_user` calls `revoke_and_forget_google` on any Google token still present before deleting rows. This also covers admin deletes, which never revoked.
7. **Backup retention, option B** (Andrew, 2026-11-20). `scripts/nuc/lib/backup.sh`: keep the newest 30 nightly folders plus the earliest folder of each of the last **3** months (was 12). A deleted account's data is then gone from the NUC and the NAS backups within about 4 months. Update `deploy/nuc-ops.md` § B7 and the privacy doc's backup section to say so.

## Non-goals

- Rewriting existing backups to remove one account (each night is one encrypted bundle; expiry is the lever).
- `users purge-smoke` (smoke accounts own no photos and no Google tokens; it stays a plain database delete).
- An LLM call already in flight when the account is deleted cannot be recalled; the check is at the start of the draft job.

## Anti-goals

- A restore that the person sees succeed, followed by the account vanishing.
- A deletion that reports done while photos remain in storage.

## Approaches

- **Chosen:** reuse the app's deletion code everywhere, and use row locks on the user to order restore, hard delete, account delete and the Google callback. Small, uses Postgres as it is, one place defines deletion.
- Rejected: a separate "purging" state column that restore refuses. It needs a migration and still needs the re-check; the lock plus the existing deadline rule gives the same order.
- Rejected: copying the storage scrub into `manage.py`. Two deletion paths drift; row 51 exists because they already did.

## Left to the builder

Test layout, and how the CLI runs the async functions (one event loop per command, engine disposed after).
