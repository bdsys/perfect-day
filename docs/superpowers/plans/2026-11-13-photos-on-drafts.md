# Adding Photos to Entries (Item 14) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One "Add photos" on every entry, offering the phone, Google Photos (Picker API) and the parent's library, plus review fixes S-5, S-7, S-8 and the shared-photo bug C-22 found while planning.

**Architecture:**
- **Ingest:** every photo, whether uploaded from a phone or copied from Google Photos, goes through one ingest service. The service encrypts the photo before storing it, makes a thumbnail, and makes a JPEG display copy for HEIC. Phone uploads stream through the API (no more presigned PUT to `tmp/`).
- **Google Photos:** a pick is a `photo_imports` row. A Celery task polls Google's session and copies the picked items one at a time. The entry page shows the import's outcome until it is dismissed.
- **Google connect:** the flow is tied to the starting browser (S-5), keeps Calendar when Photos is added, refuses a different account, and can return to the entry it started from.

**Tech Stack:** FastAPI 0.162, SQLAlchemy async, Alembic, Celery/Redis, MinIO (boto3), Pillow + pillow-heif, httpx; Next.js 15, Jest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-13-photos-on-drafts-design.md`

## Global Constraints

- Photo bytes are never stored unencrypted, never logged. Encryption uses `app/core/photo_crypto.py` (`generate_dek`, `wrap_dek(dek, owner_id)`, `encrypt_stream(..., purpose)`); a new blob type gets its own purpose constant.
- Max photo size `MAX_BYTES` = 50 MiB, enforced **while reading** the request body, not after.
- Allowed types: `ALLOWED_MIME` in `app/services/photos.py`, detected from bytes (`detect_mime`). Videos are never stored.
- Coordinates in responses stay owner-only (S-12); event addresses owner-only (S-13).
- Google Picker scope: `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`. API base `https://photospicker.googleapis.com/v1`. `pickingConfig.maxItemCount` = `"50"`. Download = `baseUrl + "=d"` with `Authorization: Bearer`. Base URLs last 60 minutes. Delete the session after copying.
- One Google connection per user (`oauth_tokens`, provider `google`). Connecting Photos never removes Calendar access; a different Google account than the connected, unrevoked one is refused.
- At most 3 phone uploads at a time; Google items copied one at a time.
- Every `Depends(get_db)` uses `scope="function"` (D-8 guard test enforces it).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `make test-all` green at the end; the real-phone checks in Task 10 are reported, not faked.

## Review Focus

1. **A co-parent's photo on the owner's entry.** The owner, other editors and (once published) viewers must see it full-size and as a thumbnail. Today `GET /v1/photos/{id}` 404s for anyone but the uploader (C-22). Task 2's tests pin each role × draft/published.
2. **Approving Photos with a different Google account while Calendar is connected.** Calendar must stay byte-for-byte as it was, and the parent is told which account to use. Task 5 pins it.
3. **The phone tab closes mid-pick.** The server finishes the copy without the browser, and the outcome shows on the entry the next time it's opened. Task 7 pins the worker running with no web polling; Task 9 pins the notice.
4. **An iPhone HEIC original from Google Photos opened full-size in Chrome.** Must display, so `kind=full` serves the JPEG display copy. Task 4 pins it.
5. **A connect link opened in another browser (S-5).** Nothing is saved, and the parent sees a plain failure. Task 6 pins it.

---

### Task 1: Schema for display copies, per-user duplicates and Google imports

**Files:**
- Create: `apps/api/alembic/versions/0013_photo_imports.py`
- Modify: `apps/api/app/models/__init__.py` (`Photo`, new `PhotoImport`)
- Test: `apps/api/tests/integration/test_photo_imports_schema.py`

**Interfaces:**
- Produces:
  - `Photo.display_s3_key: str | None`.
  - `PhotoImport` model, table `photo_imports`. Columns:
    - `id` uuid pk;
    - `user_id` FK users CASCADE;
    - `entry_id` FK entries CASCADE;
    - `google_session_id` text not null;
    - `status` String(20) with check `IN ('waiting','copying','done','failed','cancelled','expired','needs_reconnect')`, default `'waiting'`;
    - `expires_at` timestamptz;
    - `added_count` int default 0;
    - `skipped_videos` int default 0;
    - `failed_names` JSONB default `[]`;
    - `dismissed_at` timestamptz null;
    - `TimestampMixin`.
  - The photos duplicate index becomes `ux_photos_user_source_external_id` on `(user_id, source, external_id)`, unique, `WHERE external_id IS NOT NULL AND deleted_at IS NULL`. It replaces `ix_photos_source_external_id`.

- [ ] **Step 1: Write failing schema tests**: `test_photo_has_display_key`, `test_photo_imports_status_check_rejects_unknown` (IntegrityError on `status='bogus'`), `test_same_google_item_allowed_for_two_users`, `test_same_google_item_twice_for_one_user_rejected`, `test_deleted_duplicate_does_not_block_new_copy`.
- [ ] **Step 2: Run** `pytest tests/integration/test_photo_imports_schema.py -v`. Expected: FAIL (column/table missing).
- [ ] **Step 3: Write migration 0013** (upgrade and downgrade) and the model changes.
- [ ] **Step 4: Run** the same tests plus `tests/integration/test_migration*.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): photo display copies, per-user Google duplicates, photo imports table (item 14)`.

### Task 2: Shared photos can be seen (C-22) and the library sorts by an entry's date

The documented rule (`design/03-api-surface.md` § Photo authorization) was never built: `GET /v1/photos/{id}` only serves the uploader, and decrypts with the caller's id.

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py` (`get_photo`, `list_user_photos`)
- Test: `apps/api/tests/integration/test_shared_photos.py`

**Interfaces:**
- Produces:
  - `async def _readable_photo(photo_id, user, db) -> Photo | None`. Implements rule (a) the uploader, or (b) the photo is attached to a non-deleted entry in a non-deleted diary where the caller is the owner or an editor, or a viewer when that entry is published.
  - `GET /v1/photos?near=YYYY-MM-DD` sorts photos with a `taken_at` by `abs(taken_at::date - near)` ascending, then photos without `taken_at`, then `created_at DESC`. Without `near`, the order is unchanged.

- [ ] **Step 1: Write failing tests.** Parametrize over (role ∈ owner/editor/viewer) × (entry draft/published). An editor uploads and attaches a photo to the owner's entry, and the test asserts the expected status for `kind=thumb` and `kind=full`:
  - owner 200/200;
  - editor 200/200;
  - viewer 404 on a draft, 200 when published.
  - Plus `test_full_bytes_decrypt_with_uploaders_key` (the owner gets the editor's exact bytes back) and `test_stranger_gets_404`.
  - Plus `test_library_near_date_orders_closest_first`: three photos with taken_at 1, 10 and 30 days off, and one with none; `near` returns them in that order with the undated one last.
- [ ] **Step 2: Run.** Expected: FAIL with 404s for owner and editor.
- [ ] **Step 3: Implement.** `unwrap_dek(photo.dek_ciphertext, photo.user_id)`. `list_user_photos` gains `near: date | None = None`. `/photos/{id}/metadata` stays uploader-only.
- [ ] **Step 4: Run** the new tests and `tests/integration/test_photos.py`. Expected: PASS.
- [ ] **Step 5: Record C-22** in `design/code-review-2026-11.md` (Correctness): "Shared family couldn't see photos others uploaded", Fixed, tested. **Commit** `fix(api): co-parents and family can see photos on shared entries (C-22)`.

### Task 3: One ingest service; phone uploads go through the API encrypted (S-7)

**Files:**
- Create: `apps/api/app/services/photo_ingest.py`
- Modify: `apps/api/app/routers/v1/photos.py` (new `POST /v1/photos`; remove `upload-url` and `finalize`), `apps/api/app/services/photos.py` (drop `presign_put_url` if unused)
- Test: `apps/api/tests/integration/test_photo_upload_direct.py`; update `tests/integration/test_photos.py` (its `_upload_and_finalize` helper and the presign/finalize tests go; the round-trip tests use the new endpoint)

**Interfaces:**
- Produces:
  - `async def ingest_photo(db, *, owner: User, data: bytes, source: Literal["upload","google_photos"], external_id: str | None = None, taken_at_fallback: datetime | None = None) -> Photo`. It:
    - detects the type and rejects anything not allowed (`UnsupportedPhoto`);
    - parses EXIF (`taken_at` falling back to `taken_at_fallback`, plus lat and lon);
    - makes the thumbnail, then encrypts and stores the full image and the thumbnail;
    - adds a finalized `Photo` row and flushes, without committing.
    Task 4 adds the display copy here.
  - `class UnsupportedPhoto(Exception)`.
  - `POST /v1/photos`, multipart field `file`, returns 201 `PhotoOut`. The body is read in chunks and the request is refused with 413 `size_exceeds_limit` as soon as it passes `MAX_BYTES`. Errors: 415 `unsupported_mime`.

- [ ] **Step 1: Write failing tests**:
  - `test_upload_stores_only_ciphertext`: after upload, every object in the bucket decrypts and no object equals the plaintext; no `tmp/` objects.
  - `test_upload_reads_exif_time_and_gps` uses the existing `tests/fixtures/photos/` sample.
  - `test_oversize_rejected_without_storing` sends `MAX_BYTES + 1` bytes and expects 413 with no object and no row (patch `MAX_BYTES` small).
  - `test_non_image_rejected_415`.
  - `test_upload_requires_auth`.
- [ ] **Step 2: Run.** Expected: FAIL (405 or 404 on `POST /v1/photos`).
- [ ] **Step 3: Implement** `ingest_photo`, the endpoint and the removals. The orphan sweeper keeps cleaning the `tmp/` prefix for leftovers from before this change.
- [ ] **Step 4: Run** `pytest tests/integration/test_photo_upload_direct.py tests/integration/test_photos.py tests/integration/test_entry_location.py`. Expected: PASS.
- [ ] **Step 5: Record S-7** as fixed in the review doc. **Commit** `fix(api): photos upload through the API and are encrypted before storage (S-7)`.

### Task 4: HEIC photos get a JPEG display copy

**Files:**
- Modify: `apps/api/app/core/photo_crypto.py` (`PURPOSE_DISPLAY = b"display"`), `apps/api/app/services/photo_ingest.py`, `apps/api/app/routers/v1/photos.py` (`get_photo`), `apps/api/app/workers/beat_tasks.py`, `apps/api/app/workers/hard_delete.py` (delete the display object too)
- Test: `apps/api/tests/integration/test_photo_display_copy.py`; add a small HEIC fixture to `tests/fixtures/photos/` (generate with pillow-heif in the test setup if a file isn't checked in)

**Interfaces:**
- Produces:
  - `def make_display_jpeg(data: bytes, mime: str) -> bytes | None`, in `services/photos.py`. Returns JPEG bytes for `image/heic`/`image/heif` and None for browser-viewable types.
  - `GET /v1/photos/{id}?kind=full` serves the display copy (as `image/jpeg`) when `display_s3_key` is set. `kind=original` always serves the original.
  - `async def create_missing_display_copies(limit: int = 20) -> int`, run at the end of `sweep_orphaned_photos`.

- [ ] **Step 1: Write failing tests**:
  - `test_heic_upload_full_is_jpeg`: `kind=full` is `image/jpeg` and opens with Pillow.
  - `test_heic_original_still_available`: `kind=original` returns the HEIC bytes.
  - `test_jpeg_upload_has_no_display_copy`.
  - `test_backfill_adds_display_copy_to_old_heic`: a HEIC photo made via `make_photo` without a display copy gets one after `create_missing_display_copies()`.
  - `test_hard_delete_removes_display_object`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, `tests/integration/test_photos.py`, `tests/integration/test_review_fixes.py` and the hard-delete tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): HEIC photos get a JPEG copy for full-size viewing (item 14)`.

### Task 5: Google connect for Photos without breaking Calendar

**Files:**
- Modify: `apps/api/app/routers/v1/integrations.py`, `apps/api/app/services/calendar_connection.py`
- Create: `apps/api/alembic/versions/0014_photos_scope_name.py` (data-only: in `oauth_tokens.scopes_granted`, drop `photoslibrary.readonly`)
- Test: `apps/api/tests/integration/test_google_photos_connect.py`; update `tests/integration/test_calendar_connection.py` where it asserts the old scope

**Interfaces:**
- Produces:
  - `SCOPE_PHOTOS = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"`. The stored short name is `"photospicker.mediaitems.readonly"`.
  - `GET /v1/integrations/google/authorize?scopes=photos&return_to=/entries/{id}`.
    - `return_to` must start with `/` and not `//`; otherwise it is ignored and defaults to `/diaries`.
    - It rides in the signed state.
    - When an unrevoked connection exists, `login_hint=<google_email>` is added.
  - Callback rules:
    - Asked for calendar but not granted → as today (C-19).
    - Asked for photos only: if not granted → `?google=partial&missing=photos` and nothing changes; if granted → save.
    - An existing, unrevoked connection whose `google_email` differs (case-insensitive) from the consenting account → `?google=wrong_account&expected=<email>`, with nothing saved.
    - Scopes saved = Google's granted set ∪ the existing row's scopes when the account matches.
    - Redirect target = `web_base + return_to`.
  - `calendar_state` rule: no `calendar.readonly` in the scopes → `"none"` (a photos-only token, revoked or not, never says Calendar is lost); calendar scope and revoked → `"lost"`; calendar scope and not revoked → `"connected"`. Since C-19, a token without calendar can only come from a photos-only connect, so this keeps every existing case's answer.
  - `GET /v1/integrations` adds `photos: bool` (connected, unrevoked, has the picker scope) to `IntegrationOut`.

- [ ] **Step 1: Write failing tests**:
  - `test_authorize_requests_picker_scope_not_library`;
  - `test_return_to_rides_through_and_bad_values_fall_back` (`//evil.com`, `https://evil.com` → `/diaries`);
  - `test_photos_only_connect_saves_for_user_without_calendar`;
  - `test_adding_photos_keeps_calendar` (scopes contain both; tokens updated; Calendar `connected`);
  - `test_different_account_refused_and_calendar_untouched` (the row's ciphertexts, scopes and email are unchanged; redirect has `google=wrong_account`);
  - `test_photos_declined_changes_nothing`;
  - `test_photos_only_token_does_not_say_calendar_lost`;
  - `test_old_library_scope_removed_by_migration`.
  Mock the token exchange as `tests/integration/test_calendar_connection.py` does (`patch("httpx.AsyncClient.post", ...)`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, `test_calendar_connection.py` and `test_google_identity.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): connect Google Photos (Picker) without disturbing Calendar (item 14)`.

### Task 6: Google connect is tied to the browser (S-5); disconnect revokes at Google (S-8)

**Files:**
- Modify: `apps/api/app/routers/v1/integrations.py`
- Test: `apps/api/tests/integration/test_google_connect_security.py`

**Interfaces:**
- Produces:
  - `authorize` sets cookie `pd_google_connect` = `sha256(nonce)` hex: HttpOnly, SameSite=Lax, Secure unless `settings.environment` is local/test, Path `/v1/integrations/google/callback`, Max-Age 600.
  - The callback refuses a missing or mismatched cookie with `?google=failed&reason=browser_mismatch`, saving nothing, and always deletes the cookie.
  - `DELETE /v1/integrations/google`:
    1. POSTs `https://oauth2.googleapis.com/revoke` with the refresh token (else the access token), timeout 10 s;
    2. deletes the `OAuthToken` row, whatever Google answered;
    3. logs a failed revoke without the token.
    It returns 204. Calendar then reads `"none"`.

- [ ] **Step 1: Write failing tests**:
  - `test_callback_without_cookie_saves_nothing`;
  - `test_callback_with_other_browsers_cookie_saves_nothing`;
  - `test_callback_with_matching_cookie_saves`;
  - `test_cookie_cleared_after_callback`;
  - `test_disconnect_revokes_at_google_and_deletes_tokens` (assert the revoke call's form body has the decrypted refresh token, and the row is gone);
  - `test_disconnect_deletes_tokens_even_if_google_down`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Update any existing callback tests to send the cookie (helper in the test module).
- [ ] **Step 4: Run** all `tests/integration/test_*google*`, `test_calendar_connection.py` and `test_integrations*.py`. Expected: PASS.
- [ ] **Step 5: Record S-5 and S-8** fixed in the review doc. **Commit** `fix(api): Google connect is tied to the starting browser; disconnect revokes at Google (S-5, S-8)`.

### Task 7: Google Photos picks: API and copying worker

**Files:**
- Create: `apps/api/app/services/google_photos.py`, `apps/api/app/routers/v1/photo_imports.py` (register in `main.py`)
- Modify: `apps/api/app/workers/tasks.py` (new task)
- Test: `apps/api/tests/unit/test_google_photos_client.py`, `apps/api/tests/integration/test_photo_imports.py`

**Interfaces:**
- Consumes:
  - `ingest_photo` (Task 3);
  - `PhotoImport` (Task 1);
  - the picker scope name (Task 5);
  - `ensure_fresh_access_token(oauth_token, db)` (`workers/token_refresh.py`);
  - `queue_location_refresh(entry_id)` (`workers/tasks.py`).
- Produces:
  - `class GooglePhotosClient(access_token: str, transport: httpx.AsyncBaseTransport | None = None)`, with methods:
    - `create_session(max_items=50) -> PickerSession`;
    - `get_session(id) -> PickerSession`;
    - `list_items(session_id) -> list[PickedItem]` (follows `nextPageToken`, `pageSize=100`);
    - `download(item) -> bytes` (`baseUrl + "=d"`, bearer, enforces `MAX_BYTES`);
    - `delete_session(id) -> None`.
  - `PickerSession(id, picker_uri, items_set: bool, poll_interval_s: float, timeout_s: float | None, expire_time: datetime | None)` parses Google duration strings like `"5s"` and `"1.5s"`.
  - `PickedItem(id, type, create_time, base_url, mime_type, filename)`.
  - `GoogleAuthError` (401/403) and `GoogleUnavailable` (5xx, timeouts).
  - Endpoints:
    - `POST /v1/entries/{entry_id}/photo-imports` (editor or owner, Photos connected) returns 201 `{id, picker_uri, status}`. Errors: 409 `photos_not_connected`; 403 for a viewer.
    - `GET /v1/photo-imports/{id}` (the caller's own) returns `{id, entry_id, status, added_count, skipped_videos, failed_names}`.
    - `POST /v1/photo-imports/{id}/cancel`.
    - `GET /v1/entries/{entry_id}/photo-imports` returns the caller's undismissed imports for that entry, newest first.
    - `POST /v1/photo-imports/{id}/dismiss`.
  - Celery task `app.workers.tasks.process_photo_import(import_id)`:
    1. While `waiting`, polls `get_session` and re-queues itself with `countdown=poll_interval_s` until `items_set`, `expires_at`, or the status is `cancelled`.
    2. Then sets `copying` and, for each item in order:
       - video → `skipped_videos += 1`;
       - already in the library for this user (`source='google_photos'`, `external_id=item.id`, not deleted) → attach;
       - else download → `ingest_photo(source="google_photos", external_id=item.id, taken_at_fallback=item.create_time)` → attach;
       - any per-item failure → append `filename` to `failed_names`.
       Commit after each item.
    3. Then deletes the session (best effort), sets `done`, and calls `queue_location_refresh` once if anything was added.
    `GoogleAuthError` at any point → `needs_reconnect`; past `expires_at` → `expired`.

- [ ] **Step 1: Write failing unit tests** for the client with `httpx.MockTransport`:
  - `test_create_session_sends_max_items_and_parses_picker_uri`;
  - `test_duration_strings_parse`;
  - `test_list_items_follows_pages`;
  - `test_download_uses_d_param_and_bearer`;
  - `test_download_over_limit_raises`;
  - `test_401_raises_google_auth_error`.
- [ ] **Step 2: Write failing integration tests.** Patch `GooglePhotosClient` with a fake that holds a scripted session, and run the task body directly (not via the broker):
  - `test_create_import_returns_picker_uri_and_queues_task`;
  - `test_viewer_cannot_start_import`;
  - `test_not_connected_returns_409`;
  - `test_finished_pick_copies_attaches_and_counts` (2 photos + 1 video → `added_count=2`, `skipped_videos=1`, 2 `EntryPhoto` rows, session deleted, one location refresh queued);
  - `test_repicked_item_reuses_library_photo`;
  - `test_one_failed_download_is_named_and_others_succeed`;
  - `test_task_finishes_with_no_web_polling` (Review Focus 3: no GET calls made, status reaches `done`);
  - `test_revoked_token_marks_needs_reconnect`;
  - `test_expired_session_marks_expired`;
  - `test_cancel_stops_polling`;
  - `test_dismissed_import_not_listed`;
  - `test_other_users_import_404`.
- [ ] **Step 3: Run both.** Expected: FAIL (modules missing).
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** the new tests and `tests/unit/test_get_db_scope.py`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(api): pick photos from Google Photos onto an entry (item 14)`.

### Task 8: Web: "Add photos" with phone uploads and the library

**Files:**
- Create: `apps/web/src/components/AddPhotos.tsx`, `apps/web/src/components/__tests__/AddPhotos.test.tsx`
- Modify: `apps/web/src/lib/api.ts` (`photos.upload(file, onProgress)` via XHR multipart to `POST /v1/photos` with the bearer token, retrying once after a token refresh on 401; `photos.listForUser(near?)`; remove `requestUploadUrl`/`uploadFile`/`finalize`), `apps/web/src/components/PhotoUploadButton.tsx` (use `photos.upload`, allow `multiple` via prop), `apps/web/src/app/entries/[entryId]/page.tsx` (replace the bottom "Attach photo" block with `<AddPhotos>` near the top, shown when `entry.can_edit`), `apps/web/src/components/PhotoLightbox.tsx` (no change expected; confirm it uses `kind=full`)
- Test: update `apps/web/e2e/photo-upload.spec.ts`

**Interfaces:**
- Produces: `<AddPhotos entry={Entry} onChanged={(e: Entry) => void} />`, a button "Add photos" opening a panel with:
  - **"From this phone"**: a file input with `multiple` and `accept="image/*"`. At most 3 uploads at a time. Each file is a row with its name and progress, then "Added", or its error with "Try again". Each upload attaches on success. The entry is refetched once when the batch ends.
  - **"From your library"**: `listForUser(entry.entry_date)`, excluding photos already attached. Tapping attaches, and the panel stays open.
  - **"From Google Photos"**: a slot that Task 9 fills.

- [ ] **Step 1: Write failing Jest tests** (mock `api`):
  - `uploads several files three at a time` (5 files: at most 3 `upload` calls pending at once);
  - `a failed file shows its name and Try again re-uploads only it`;
  - `library is asked for photos near the entry date and hides attached ones`;
  - `attaching from the library keeps the panel open`.
- [ ] **Step 2: Run** `npx jest AddPhotos`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update the e2e test** `photo-upload.spec.ts` for the new button and multi-select (`setInputFiles` with 2 files → 2 thumbnails in the strip). Run `npx jest` and the e2e spec against the test stack (`make test-e2e` or the targeted run used before). Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): Add photos from the phone (several at once) or the library (item 14)`.

### Task 9: Web: Google Photos button, waiting state and results notice

**Files:**
- Create: `apps/web/src/components/GooglePhotosPick.tsx`, `apps/web/src/components/PhotoImportNotice.tsx`, tests for both in `__tests__/`; `apps/web/e2e/google-photos-pick.spec.ts` (all API calls mocked with `page.route`, like `pick-waits-for-draft.spec.ts`)
- Modify: `apps/web/src/lib/api.ts` (`photoImports.create/get/cancel/listForEntry/dismiss`; `integrations.getGoogleAuthUrl(scopes, returnTo?)`; `Integration.photos`), `AddPhotos.tsx` (slot), the entry page (`<PhotoImportNotice>` above the photo strip; show the `google=` result message on the entry page as the diaries page does; move `googleStatusMessage` to `src/lib/googleStatus.ts` and add `wrong_account` → "Use the Google account you connected for Calendar ({expected})." and `browser_mismatch` → "That Google link was opened in a different browser. Start again here.")

**Interfaces:**
- Produces:
  - `<GooglePhotosPick entry onDone />`:
    - Not connected → "Connect Google Photos" → `getGoogleAuthUrl('photos', '/entries/{id}')` → navigate.
    - Connected:
      1. Shows "Search Google Photos for {entry date, e.g. 16 May 2026}".
      2. On tap, opens `window.open('', '_blank')` **synchronously**.
      3. Creates the import, then sets the new tab's location to `picker_uri + '/autoclose'`; if the tab was blocked, shows a link to open it instead.
      4. Shows "Waiting for you to finish in Google Photos…" with Cancel, polling `photoImports.get` every 3 s until not `waiting`/`copying`.
  - `<PhotoImportNotice entryId onDismiss />`: lists the caller's undismissed imports for the entry, for example "12 photos added from Google Photos · 2 videos skipped · 1 couldn't be copied (IMG_1234.HEIC): pick it again from Google Photos". `needs_reconnect` shows a reconnect button. Dismiss calls `dismiss`.

- [ ] **Step 1: Write failing Jest tests**:
  - `opens the tab inside the click before any await`;
  - `blocked tab shows a link to open Google Photos`;
  - `not connected starts Google connect with the entry as return path`;
  - `shows the entry date to search for`;
  - `notice wording for added, skipped and failed`;
  - `needs_reconnect offers reconnect`;
  - `dismiss hides the notice`.
- [ ] **Step 2: Write the failing e2e test**: start a pick, have the mocked import reach `done`, see the notice and the new photos, dismiss, then reload and the notice stays gone. Also `wrong_account` on return shows the account message.
- [ ] **Step 3: Run.** Expected: FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** `npx jest`, `npx tsc --noEmit`, `npx eslint src` and the e2e spec. Expected: PASS.
- [ ] **Step 6: Commit** `feat(web): add photos from Google Photos, with a results notice (item 14)`.

### Task 10: Docs, the full suite and the real-phone checks

**Files:**
- Modify: `design/03-api-surface.md` (new endpoints; `kind=full|thumb|original`; upload-url/finalize removed; photo authorization now built), `design/02-data-model.md` (`display_s3_key`, `photo_imports`, index), `design/05-google-oauth-integrations.md` (Picker flow, return path, account rule, S-5 cookie, S-8 revoke), `POC_PHASE2_TODO.md` (item 14 status), `docs/local-dev.md` (enable the Photos Picker API and add the scope in Google Cloud), `design/code-review-2026-11.md` (S-5, S-7, S-8, C-22 statuses)

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Run** `make test-all` and `cd apps/web && npx jest`. Expected: all green; record the counts.
- [ ] **Step 3: Real-phone checks with Andrew**, recorded in the PR body with what was seen. Do not claim any you didn't see:
  1. On iPhone Safari and Android Chrome (whichever Andrew has), can you pick several photos, and does an upload keep GPS (does the entry's place change to the photo's)?
  2. Does the Google Photos picker open from the phone and return to the tab?
  3. Does a HEIC photo show full-size?
  4. Is the Picker API enabled in Google Cloud and the scope on the consent screen?
- [ ] **Step 4: Commit** `docs: item 14 adding photos, with S-5, S-7, S-8, C-22`.
