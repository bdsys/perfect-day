# Item 14 Follow-ups (Triage 2026-11-14, Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the triage items marked ⓑ: the fixes to item 14's code and the parts that build on it. They ship inside PR #51, before it merges.

**Architecture:** Each task is a small, independent fix with a failing test first. There is one migration, 0016, covering a display-copy attempt column and an index. Everything else changes the photo import worker, the Google client, Google connect, photo storage, the web client's token refresh, and the entry and diary pages.

**Tech Stack:**
- API: FastAPI 0.162.5, SQLAlchemy async, Alembic (head 0015), Celery, Pillow and pillow-heif, pytest on Python 3.12.
- Web: Next.js 15, Jest, Playwright.

**Spec:** `design/code-review-2026-11.md`, section "Triage (2026-11-14)", rows TR-10, TR-29 (client half), TR-30 to TR-36 and TR-40. Item 14's design is `docs/superpowers/specs/2026-11-13-photos-on-drafts-design.md`.

## Global Constraints

- **Branch:** work on `feat/14-photos-on-drafts`. `main` (with PR #52) is already merged into it.
- **Photo data:**
  - Photo bytes are never stored unencrypted and never logged.
  - Google base URLs and tokens never reach logs.
  - Coordinates in responses stay owner-only (S-12 and TR-20). Event addresses stay owner-only (S-13).
- **Code rules:** every `Depends(get_db)` uses `scope="function"`.
- **Copy:** user-facing text is plain, short English, and never a raw error code.
- **Tests:**
  - Tests must not print "coroutine never awaited". Patch async functions with `AsyncMock`.
  - Stage files by explicit path. Never use `git add -A` or `git commit -a`.
  - Never run two implementers at once.
- **Commits:** each message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Finish:** `make test-all` is green, and so is `cd apps/web && npx jest`.

## Review Focus

1. **A parent double-taps an upload, then the attach fails.** "Try again" re-attaches the photo that was already uploaded. It never uploads a second copy. Pinned in Task 4.
2. **A co-parent views the owner's iPhone JPEG full-size.** Location must be gone, including GPS hidden in maker notes or an embedded thumbnail, and orientation must be kept. Pinned in Task 2.
3. **Disconnecting Google while a backfill or scan is running.** The run ends `failed` with a readable message. It is never left "running". Pinned in Task 3.
4. **Opening the connect link with an old `pd_google_connect` cookie still set, mid-deploy.** The new `__Host-` cookie flow works, and the old cookie is ignored. Pinned in Task 3.
5. **A Google import finishing while the parent is in Edit mode.** The photo strip shows the new photos once they leave Edit. Pinned in Task 6.

---

### Task 1: The web client shares one token refresh (TR-29, client half)

**Files:**
- Modify: `apps/web/src/lib/api.ts`. That means `refreshAccessToken` (line 76), the inline refreshes in `apiFetchBlob` (about line 98) and `apiFetch` (about line 139), and the upload retry (about line 722).
- Modify: `apps/web/src/lib/auth-context.tsx:30`, which refreshes on load.
- Test: `apps/web/src/lib/__tests__/api.test.ts`

**Interfaces:**
- Produces: `refreshAccessToken(): Promise<boolean>`, exported, and single-flight. While a refresh is in flight, every caller gets the same promise. The next refresh starts only after that one settles. Every refresh in the web app goes through it.

- [ ] **Step 1: Write the failing tests.**
  - `three 401s at once make one refresh call`: three `apiFetch` calls each get 401, then 200 after the refresh. Mocked `fetch` sees exactly one `POST /v1/auth/refresh`, and all three calls resolve.
  - `a refresh after the first settles starts a new one`.
  - `auth-context's initial refresh uses the shared refresh`: assert through the module mock that `refreshAccessToken` is called.
- [ ] **Step 2: Run them.** `cd apps/web && npx jest api`. Expected: FAIL, with 3 refresh calls.
- [ ] **Step 3: Implement.** Use a module-level `let inflight: Promise<boolean> | null`, cleared in `finally`. Replace the two inline refresh blocks with calls to it.
- [ ] **Step 4: Run.** Run `npx jest`, `npx tsc --noEmit` and `npx eslint src --max-warnings 0`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(web): one shared token refresh, so parallel requests can't race (TR-29)`.

### Task 2: Shared photos keep only orientation from EXIF (TR-30)

**Files:**
- Modify: `apps/api/app/services/photos.py` `strip_location` (lines 136-168), the JPEG branch.
- Test: `apps/api/tests/unit/test_photo_strip.py`. Create it, or extend the existing strip tests found with `grep -rn strip_location tests`.

**Interfaces:**
- Produces: the same signature. For JPEG, the output EXIF holds only the orientation tag (0x0112) when the source has one, and nothing else. No MakerNote (0x927C), no GPS IFD, no IFD1 thumbnail. ICC is kept and quality stays `"keep"`. Non-JPEG behaviour is unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_jpeg_keeps_only_orientation`: the source has Orientation 6, Make "Apple", MakerNote bytes, GPS and an IFD1 thumbnail. After stripping, `getexif()` has keys `{0x0112}` with value 6, `get_ifd(0x8825)` is empty, and the raw bytes contain no `b"Apple"` and no second `FFD8` marker inside APP1.
  - `test_jpeg_without_exif_stays_without`.
- [ ] **Step 2: Run them.** Expected: FAIL, because Make and MakerNote are kept.
- [ ] **Step 3: Implement.** Build a fresh `Image.Exif()`. Copy only 0x0112, then save with `exif=new.tobytes()` when it isn't empty.
- [ ] **Step 4: Run.** Run `pytest tests -k "strip or shared_photos or photo_display" -W error::RuntimeWarning`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(api): shared photos keep only orientation from EXIF (TR-30)`.

### Task 3: Google connect and disconnect edge cases (TR-10, TR-33, TR-40)

**Files:**
- Modify: `apps/api/app/routers/v1/integrations.py`:
  - `CONNECT_COOKIE` and `CONNECT_COOKIE_PATH` (lines 33-34);
  - `_clear_connect_cookie` (line 92);
  - the `set_cookie` call (about line 202);
  - the callback compare (about line 292);
  - `revoke_google` (line 404).
- Create: `apps/api/app/services/google_revoke.py`
- Modify: `apps/api/app/routers/v1/auth.py` `delete_account` (about lines 408-432).
- Modify: `apps/api/app/workers/tasks.py`. The `scalar_one()` reads of the OAuth token in `_scan_diary` (line 164) and in the backfill task (line 914) become `scalar_one_or_none()`.
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx` `connectCalendar` (line 276).
- Test:
  - `tests/integration/test_google_connect_security.py`, updated;
  - `tests/integration/google_helpers.py`, where `connect_cookie_for` changes to the new name;
  - `tests/integration/test_account_deletion_google.py`, new;
  - a scan/backfill test;
  - the diary page Jest test.

**Interfaces:**
- Produces:
  - `async def revoke_and_forget_google(db: AsyncSession, token: OAuthToken, *, user_id: uuid.UUID) -> None` in `services/google_revoke.py`. This is the body `revoke_google` has today: POST to Google's revoke endpoint, log only the user id and the error type on failure, then delete the row. `revoke_google` and `delete_account` both call it.
  - `CONNECT_COOKIE = "__Host-pd_google_connect"` and `CONNECT_COOKIE_PATH = "/"`. A `__Host-` cookie must have Secure set, path `/` and no Domain. The callback compares bytes: `hmac.compare_digest(presented.encode(), expected.encode())`.

Rulings:
- **Account deletion now revokes at Google and deletes the token row**, as disconnect does. Restoring the account inside the 7-day window then needs a Google reconnect. That is acceptable: restore is item 29's design, and S-8 already behaves this way.
- **Scan or backfill finding the token row gone** (because the parent disconnected mid-run) is treated the same as a disconnected calendar:
  - for a scan, the `calendar_disconnected` error;
  - for a backfill, `failed` with the existing message `"No valid Google OAuth token"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_connect_cookie_is_host_prefixed`: `authorize` sets `__Host-pd_google_connect`, with `Path=/`, `Secure`, no `Domain`, `HttpOnly` and `SameSite=Lax`.
  - `test_old_cookie_name_is_ignored`: a callback carrying only the old `pd_google_connect` gets the `browser_mismatch` redirect.
  - `test_non_ascii_cookie_is_a_mismatch_not_500`: cookie value `"é"` gives a 302 to `browser_mismatch`, and the cookie is cleared.
  - `test_account_deletion_revokes_at_google_and_forgets_tokens`: with the revoke POST mocked, `DELETE /v1/auth/account` calls it once with the refresh token, and no `oauth_tokens` row remains. With Google down, the row is still gone.
  - `test_scan_after_disconnect_mid_run_reports_disconnected`: delete the token row between the two reads (patch `ensure_fresh_access_token` to delete it first, or delete it between steps). The run ends `partial` with `calendar_disconnected`, never `running`.
  - `test_backfill_after_disconnect_mid_run_fails_cleanly`: same idea. The status is `failed`.
  - Jest: `connectCalendar` double-tap makes one `getGoogleAuthUrl` call and disables the button. Use the same pattern as `GooglePhotosPick`'s `connectingRef`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as specified. Update every test that sets the old cookie name; `google_helpers.connect_cookie_for` returns the new name.
- [ ] **Step 4: Run.** Run `pytest tests -k "google or connect or account or scan or backfill" -W error::RuntimeWarning`, then `npx jest diaries`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix: __Host- connect cookie, account deletion revokes Google, disconnect mid-scan ends cleanly (TR-10, TR-33, TR-40)`.

### Task 4: Phone uploads never hang and never duplicate (TR-31)

**Files:**
- Modify: `apps/web/src/lib/api.ts`, `photos.upload` (about lines 690-730).
- Modify: `apps/web/src/components/AddPhotos.tsx`, the row state and Try again.
- Test: `apps/web/src/components/__tests__/AddPhotos.test.tsx` and `src/lib/__tests__/api.test.ts`.

**Interfaces:**
- Produces:
  - `photos.upload` sets `xhr.timeout = UPLOAD_TIMEOUT_MS` (exported constant `120000`). A timeout settles the call as a network failure, so the slot frees.
  - An `AddPhotos` row remembers `photoId` once the upload succeeds. "Try again" on a row that has a `photoId` only re-runs attach.

- [ ] **Step 1: Write the failing tests.**
  - `upload sets a timeout and a timeout frees the slot`: drive the fake XHR's `ontimeout`. The row shows the readable network-failure message, and the next queued file starts.
  - `Try again after an attach failure re-attaches without re-uploading`: upload succeeds, attach rejects once, then Try again is clicked. `photos.upload` was called once and `attach` twice.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Run `npx jest`, tsc and eslint, plus `npx playwright test photo-upload` the way the Makefile runs e2e. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(web): uploads time out instead of hanging; Try again never uploads a duplicate (TR-31)`.

### Task 5: Google import worker hardening (TR-32)

**Files:**
- Modify: `apps/api/app/workers/photo_imports.py`:
  - `_set_status` (line 110);
  - its callers (lines 130, 162, 171, 184, 216, 223, 234, 239, 289);
  - the copy loop (from about line 250).
- Modify: `apps/api/app/services/google_photos.py`:
  - `create_session`, `get_session` and `_session`;
  - `download` (about line 182).
- Test: `tests/integration/test_photo_imports.py` and `tests/unit/test_google_photos_client.py`.

**Interfaces:**
- Produces:
  - `async def _set_status(db, imp, status: str, *, only_from: tuple[str, ...] = ("waiting", "copying")) -> bool`. It runs `UPDATE photo_imports SET status=:s WHERE id=:id AND status IN only_from`, then refreshes `imp`. It returns whether the row changed, and logs `photo_import_finished` only when it did. A cancel or a sweep is never overwritten.
  - `_session` and `_item` raise `GoogleError("unexpected reply")` when a required key is missing. The router maps that to the existing 503 path, never a 500.
  - In `download`, the bearer token goes only to hosts that end in `.googleusercontent.com`, `.google.com` or `.googleapis.com`. Any other host raises `GoogleError("unexpected host")`, and the item is recorded as failed. Only the host name is logged, never the URL.
  - The copy loop re-checks edit rights once per item, using `_get_entry_or_404(..., require_editor=True)`. On losing them, it stops with status `failed` and keeps the photos already added.

Ruling: Google's docs show `lh3.googleusercontent.com` in examples but don't promise a host (<https://developers.google.com/photos/picker/guides/media-items>). The allowlist is therefore Google's domains, not one host. httpx already drops `Authorization` on a cross-origin redirect. The cost if wrong is a Google host change outside these domains, which would fail picks visibly. It would not leak the token.

- [ ] **Step 1: Write the failing tests.**
  - `test_cancel_is_not_overwritten_by_done`: cancel while the last item copies, and the final status is `cancelled`.
  - `test_sweep_failed_is_not_overwritten`.
  - `test_create_session_missing_field_is_503_not_500`: Google returns `{}`, and `POST /v1/entries/{id}/photo-imports` gives 503 with the existing readable code.
  - `test_download_refuses_unknown_host`: baseUrl `https://evil.example/x` raises `GoogleError`, and the mock transport never sees a request.
  - `test_download_allows_googleusercontent`.
  - `test_losing_edit_rights_mid_copy_stops`: drop the permission after item 1. Item 2 isn't attached, the status is `failed`, and item 1's photo is kept.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Run `pytest tests -k "photo_import or google_photos or sweep" -W error::RuntimeWarning`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(api): import status writes can't overwrite a cancel; token only to Google hosts; edit rights per item (TR-32)`.

### Task 6: Entry page polish for Google imports (TR-34)

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`. `PhotoImportNotice` is at line 447, and the `editing` state is at line 41.
- Modify: `apps/web/src/components/PhotoImportNotice.tsx`. The swallowed catches are at lines 76, 96, 108 and 121.
- Modify: `apps/web/src/components/GooglePhotosPick.tsx`. The list failure is at lines 47-48, `window.open` at line 78, and the in-progress copy at line 119.
- Test: the Jest tests for both components and the page.

**Interfaces:**
- Produces:
  - **Notice placement.** `PhotoImportNotice` stays mounted in Edit mode, outside the `editing` ternary and visually hidden while editing. Polling continues, and `onFinished` refreshes `entry.photos`.
  - **Polling errors.** A 404 while polling ends that import's in-flight state. Three poll failures in a row show `"We can't check on your Google Photos pick right now."` and stop polling until the next page load.
  - **`GooglePhotosPick` phases.** A failed `integrations.list` shows `"Couldn't check your Google connection. Try again."` with a retry button. It does not show "Connect Google Photos". This is a new phase, `"error"`.
  - **Picker tab.** The tab is opened as now, then `tab.opener = null` is set before it navigates.
  - **In-progress text.** While the import is copying, the line reads `"Copying your photos from Google Photos…"`. `"Google Photos is open. Finish your pick there."` shows only while it is waiting.

- [ ] **Step 1: Write the failing tests**, one per bullet. Assert the exact strings.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Run `npx jest`, tsc and eslint, plus `npx playwright test google-photos-pick`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(web): Google import notice survives Edit, poll errors end, honest connection and copy states (TR-34)`.

### Task 7: Photo storage hardening (TR-35, TR-36)

**Files:**
- Create: `apps/api/alembic/versions/0016_display_attempts_and_photo_index.py`. It adds `photos.display_attempted_at timestamptz NULL` and `ix_entry_photos_photo_id` on `entry_photos(photo_id)`.
- Modify: `apps/api/app/models/__init__.py` (the new column).
- Modify: `apps/api/app/workers/beat_tasks.py` `create_missing_display_copies` (lines 163-215).
- Modify: `apps/api/app/services/photo_ingest.py`, where a display-copy failure is silent (about lines 61-63).
- Modify: `design/03-api-surface.md`. A photo on a deleted diary stays readable by its uploader, because photos belong to the uploader's library.
- Test:
  - `tests/integration/test_photo_display_copy.py`;
  - `tests/integration/test_migrations.py`, or the existing migration test pattern found with `grep -rn alembic tests`.

**Interfaces:**
- Produces:
  - The backfill orders by `display_attempted_at NULLS FIRST, created_at`. It sets `display_attempted_at = now()` on every attempt, success or failure. A photo that failed is retried only after every photo not yet tried.
  - Ingest logs `display_copy_failed` with `photo_id` and `error_class` only.

- [ ] **Step 1: Write the failing tests.**
  - `test_bad_photos_do_not_starve_new_ones`: 20 photos whose objects are missing, plus 1 good one, all older than it. `limit=20`, two sweeps. The good one gets its display copy on the second sweep at the latest, and the bad ones carry `display_attempted_at`.
  - `test_ingest_logs_display_failure`.
  - `test_migration_0016_up_down`, following the existing migration test pattern.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Also fix the doc sentence (TR-36).
- [ ] **Step 4: Run.** Run `pytest tests -k "display or migration or ingest" -W error::RuntimeWarning`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `fix(api): display backfill tries new photos before retrying bad ones; log display failures; index entry_photos.photo_id (TR-35, TR-36)`.

### Task 8: Docs and the full suite

**Files:**
- Modify: `design/code-review-2026-11.md`, the TR-10, TR-29, TR-30 to TR-36 and TR-40 cells ("Built on feat/14-photos-on-drafts"). Do this only in the Triage section.
- Modify: `design/05-google-oauth-integrations.md` (the `__Host-` cookie, and account deletion revoking at Google).
- Modify: `design/02-data-model.md` (`display_attempted_at`, the index).
- Modify: `design/08-security-privacy.md` (deletion revokes at Google, so restore needs a reconnect).
- Modify: the PR #51 body. Add a "Plan B" section listing the fixes, and every Plan B ruling and deferred minor.

- [ ] **Step 1: Update the docs.**
- [ ] **Step 2: Run** `make test-all` and `cd apps/web && npx jest`. Expected: all green. Record the counts.
- [ ] **Step 3: Commit.** Message: `docs: item 14 follow-ups from the 2026-11-14 triage`.
