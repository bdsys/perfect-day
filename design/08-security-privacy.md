# Security & Privacy Design

## Photo encryption at rest

Two-layer key hierarchy, app-level. All downloads proxied through the API (no signed URLs).

```
master_secret   = loaded at process start from a secret store (see below)
KEK for user U  = HKDF-SHA256(master_secret, salt=user_id, info="photo-kek")
DEK for photo P = random 32 bytes generated at upload time

On ingest:
  # Photos are encrypted in 1 MiB chunks; each chunk has its own AES-256-GCM tag.
  # Nonce for chunk i = HKDF-SHA256(DEK, info="chunk-nonce", salt=i.to_bytes(8, 'big'))
  for i, chunk in enumerate(photo_bytes, chunk_size=1_048_576):
    ciphertext_chunk = AES-256-GCM(key=DEK, nonce=chunk_nonce(i), plaintext=chunk)
  full_ciphertext = [chunk_count(4 bytes) || chunk_size(4 bytes) || ciphertext_chunks...]
  wrapped_DEK = AES-256-GCM(KEK, DEK)  [nonce prepended]
  Object store (Garage): store full_ciphertext at {user_id}/{photo_uuid}.enc
  Postgres: photos.dek_ciphertext = [key_version_byte || nonce || wrapped_DEK]

On download:
  GET /v1/photos/{id}
  → backend retrieves full_ciphertext from the object store
  → unwraps DEK with KEK
  → for each chunk: verify GCM tag, then stream verified plaintext to client
  (memory use is bounded to one chunk at a time regardless of photo size)
```

**Why chunked encryption:** whole-blob AES-256-GCM cannot be safely streamed — the auth tag covers the entire ciphertext and must be verified before any plaintext is emitted. On a memory-constrained host, loading the entire plaintext before sending would OOM under concurrent loads. Chunked encryption verifies and emits one chunk at a time.

**`master_secret` loading:** `master_secret` is loaded at process start from a secret store, not a plain environment variable on the same host as the data. Two supported backends:
- **Single-host (home-lab):** `sops`-encrypted secrets file unlocked by a YubiKey at boot. The decrypted value is passed to the process as an environment variable and never written to disk at runtime. This is a documented compromise — see `deploy/nuc.md`.
- **Cloud:** managed secret manager (AWS Secrets Manager, GCP Secret Manager, 1Password Connect). Production-tier deployments must use a KMS-backed KEK.

The runtime never persists the unwrapped secret to disk.

- No signed URLs: uploads and downloads both go through the API (row 43 removed the unused public endpoint).
- Thumbnails encrypted with the same DEK; served via `/v1/photos/{id}/thumbnail` with same decrypt-and-stream path.
- Key version prefix in `dek_ciphertext` supports future master_secret rotation (re-wrap DEKs in Postgres; ciphertext in the object store unchanged).
- OAuth tokens use a **separate** `master_secret` (different secret store entry) and the same AES-256-GCM pattern.

## Hybrid deployment: secret and DEK boundaries

In hybrid mode (NUC + CX21), the key hierarchy boundaries change. This section documents what is and is not present on the CX21 cloud edge.

**Default mode (NUC reachable):**
- `master_secret` lives exclusively on the NUC (sops+YubiKey). The CX21 never holds it.
- The CX21 cannot decrypt photos on its own. For each photo download, the CX21 calls the NUC's internal DEK-unwrap RPC (`POST https://10.42.0.1:8443/internal/unwrap-dek`) over a WireGuard tunnel with mutual TLS.
- The NUC derives the per-user KEK from `master_secret`, decrypts the wrapped DEK, and returns only the raw DEK.
- The CX21 caches the DEK in process memory with a 15-minute TTL. On user logout, cached DEKs for that user are invalidated. The DEK is never written to disk on the CX21.
- OAuth token decryption still runs on the NUC (Celery worker) — no change from the NUC-only deployment.

**Escalation (operator promotes CX21 to primary):**
- The operator pastes `master_secret` into the CX21 process environment from a sops-encrypted backup stored in 1Password.
- This is a documented, accepted privacy degradation: `master_secret` is now present on a cloud VPS, not just the home-lab host.
- Recovery requires `master_secret` rotation (generate new 32-byte secret, re-wrap all `dek_ciphertext` rows) and removal of `master_secret` from the CX21 environment.
- The promotion event and recovery are both recorded in the audit log.
- See `deploy/hybrid.md` § Escalation runbook and `design/secrets.md` § Hybrid escalation for the full procedure.

**What the CX21 holds at all times (even in default mode):**
- Postgres replica password
- Cloudflare R2 access key + secret key
- WireGuard private key
- JWT verification key (public or symmetric for HMAC — used to verify tokens, not to sign them)
- mTLS client certificate (for DEK-unwrap RPC)

**What the CX21 never holds in default mode:**
- `master_secret`
- `oauth_token_secret`
- JWT signing key (CX21 verifies tokens only; the NUC FastAPI signs them)



| Token | TTL | Storage |
|---|---|---|
| Access token | 15 minutes | Memory only (browser); `expo-secure-store` (Expo) |
| Refresh token | 30 days rolling | `HttpOnly SameSite=Strict` cookie (web); `expo-secure-store` (Expo) |

Refresh token rotation: every `POST /v1/auth/refresh` issues a new token, invalidates the old one. Conflict detection: if a revoked token from a family is reused, entire family is revoked (all devices forced to re-login). Stored in `refresh_tokens` table (token_hash only, never plaintext).

Admin re-auth: `POST /v1/auth/reauth` verifies password, stores `reauth:{user_id}:{session_id}` in Redis with 15-min TTL. Destructive admin endpoints check for valid reauth key.

## Data deletion flow

### Single entry

Soft delete (`deleted_at`), indefinite. Restoreable from UI. Photos unaffected.

### Diary deletion (30-day grace)

```
Day 0:   deleted_at set, hard_delete_after = now() + 30d, scan disabled,
         confirmation email sent, shared-member notification fired.
Day 0–29: restorable via POST /v1/diaries/{id}/restore.
Day 28:  deletion_grace notification.
Day 30:  Celery process_hard_deletes():
           delete entry_photos, enrichments, events, llm_generations,
           entries, entry_edit_diffs, scan_runs, backfill_runs,
           diary_permissions, invitations, diary_calendar_filters.
           Photos deleted from the object store + photos table only if no other diary
           links them (cross-diary check: an entry_photos row on an
           entry in another diary keeps the photo).
           Hard delete diaries row. Write audit_log.
```

### Account deletion (7-day grace)

```
Day 0:   deleted_at set, hard_delete_after = now() + 7d.
         Immediately: revoke all refresh_tokens. Revoke the Google token at
         Google and delete the oauth_tokens rows, so restoring within the
         7 days needs a Google reconnect.
         Disable all scans. Confirmation email + restore link sent.
         Shared-diary members notified: "export your content now."
Day 6:   deletion_grace notification (last chance).
Day 7:   Celery process_hard_deletes():
           Hard delete all owned diaries (cascade per diary flow above).
           Delete notifications, notification_preferences, social_identities,
           oauth_tokens, magic_link_tokens, refresh_tokens.
           Scrub the object store: delete everything under {user_id}/* prefix.
           Anonymize audit_log rows (null user_id, preserve action/timestamp).
           Hard delete users row.
```

### Account deletion with hand-over (built 2026-11-22)

Deletion cascades (above); a hand-over is the only exception, and only by the owner's explicit choice. The default for every diary is Delete.

- **The choice.** When the owner deletes their account, `GET /v1/auth/account/handover-options` lists each owned diary that has an active member. For each one the owner picks a recipient or leaves it as Delete. `DELETE /v1/auth/account` takes `{handovers: {diary_id: user_id}}`, validates every pair before writing anything, and records the choice in `diaries.handover_to_user_id`.
- **The grace week.** The recipient sees "[Owner] is deleting their account. [Diary] becomes yours on [date]." and can **Decline** (`POST /v1/diaries/{id}/handover/decline`); the diary is then deleted with the account. Removing the recipient from the diary also clears the choice.
- **Restore.** Restoring the account clears every hand-over choice: nothing changes hands.
- **Day 7.** The hourly hard delete first moves the chosen diaries (`workers/handover.py`): the recipient becomes owner, `owned_since` is set, their membership row goes, the old owner's calendar filters, unattached events, scan cursors, pending backfills and unaccepted invites are removed, scanning is turned off (the new owner connects their own Google account), and an `audit_log` row `diary.handover` is written. If the recipient is gone, no longer a member or themself being deleted, the choice is cleared and the diary is deleted as without one.
- **Photos.** Photos stay with the diary they are in. Each photo of the departing user attached to an entry in someone else's diary moves to that diary's owner (earliest attachment wins): the DEK is re-wrapped for the new owner and the objects are copied server-side first, so a row only ever points at objects that exist. The old copies go with the `{user_id}/*` prefix scrub at the end. A Google Photos duplicate the new owner already has is merged into their copy. A photo whose original is missing from the store is not moved.
- **Retry-safe, not one transaction.** Each diary and each photo is its own step that re-checks state, so any step may fail and the next hourly run finishes the job.
- **Single-flight.** The hourly hard-delete pass holds a Postgres advisory lock for the whole pass (`hard_delete_lock`); a second pass skips. The admin CLI's hard delete waits for it. Without it, one deletion could move a photo to an account the other deletion is erasing.
- **GPS after a move.** The new owner sees the photo's GPS: the original with its EXIF, plus latitude and longitude in their photo library and `/metadata`. This follows "coordinates to the owner only": the owner of the photo changed.
- **Wrong-email refusal.** `invite_wrong_email` and `invite_expired` name the owner by display name, or by a masked address (`a•••@example.com`) when the owner has no display name. The full address is never sent to someone who is not the invitee.
- A restore from backup rolls hand-overs back with everything else; tracker row 67 covers re-applying them.

Cross-diary photo check: the `photos` row and its stored objects are deleted only when no entry in another diary still links the photo through `entry_photos` (`apps/api/app/workers/hard_delete.py`). The `diary_photos` table is not consulted.

## Diary sharing (built 2026-11-22)

- **Invite tokens.** Only the SHA-256 hash is stored (`invitations.token`). The raw token appears in exactly two responses: creating an invite and making a new link. It lives in the page's memory only, never in storage. A new link replaces the old token, so the old link stops working. An invite works once, for 7 days.
- **Email binding.** An invite works only for a signed-in account whose email matches `invited_email`, case-insensitive. A wrong or signed-out visitor learns nothing about the diary: not its name, not its owner. The wrong-email page shows a masked address (`m•••@example.com`).
- **Accepted gap: unverified email (Andrew).** Registration does not verify an email address, so someone who learns a link could register the invited address first. Andrew, 2026-11-20: "the risk seems low" (the link goes to one person and works once). Project 2 (email verification) closes it.
- **Link in the URL (open).** Invite links carry the raw token in the URL path (`/invite/{token}`), so it can appear in logs. Three places record it: the proxy's access log, uvicorn's own access log (`apps/api/Dockerfile` line 18 and `docker-compose.yml` line 94 run it with access logging on), and the API's error handler (`apps/api/app/core/errors.py`, which logs `request.url.path`). It is single-use, valid 7 days and bound to the invited email. Whether to redact it from these logs is an open decision; nothing redacts it today.
- **What people see.** Viewers see published entries and their photos only, with no AI badge. Coordinates go to the owner only. Removing a person, or a person leaving, ends access at once.
- **Limits and downgrade.** Enforced in the API, per diary, members plus pending invites: Free 3, Plus 10, Family unlimited; editors Family only. On a downgrade nobody loses access; an editor whose owner is not on Family acts as a viewer.

## GDPR/CCPA posture

| Item | PoC status | Pre-public action required |
|---|---|---|
| Right to erasure | ✅ Covered by deletion flow | — |
| Right to access/portability | ❌ Not built | Add `GET /v1/auth/me/export` (JSON + photo ZIP) |
| Consent for photo processing | ❌ Not built | Explicit consent UI; document lawful basis |
| Children's data (COPPA/GDPR Art.8) | ℹ️ Parent-operator model; child is not a user | Legal review before public launch |
| Privacy policy | ❌ Not written | Required before public launch |
| DPAs with vendors | ❌ Not executed | Anthropic, Google, SendGrid, Expo each need a DPA for GDPR |
| Data residency | ℹ️ Single-host only for PoC | EU public launch needs EU data residency |
| Breach notification | ❌ No runbook | 72-hour GDPR requirement; runbook + contact email needed |

## Auth endpoints rate limiting

- `/auth/login`, `/auth/register`, `/auth/magic-link/request`: 10 req/min per IP.
- Magic link: max 3 requests per email per 10 minutes.

## Magic link tokens

32-byte random (URL-safe base64). Hash-only storage in `magic_link_tokens`. 15-min TTL. Single-use (`consumed_at` set on first use). The `email` field is lowercased on insert (`LOWER(email)`) — `citext` handles case-insensitive comparison, but storing in canonical lowercase prevents display inconsistencies in the admin UI.

## CSRF

`HttpOnly SameSite=Strict` cookie on refresh tokens. Custom `Authorization` header on API calls (not form-submittable). No additional CSRF token needed for the API in general.

**`/v1/auth/refresh` CSRF protection:** this endpoint accepts the refresh token from the HttpOnly cookie without an Authorization header. The `SameSite=Strict` attribute prevents cross-origin POSTs from including the cookie, which is the primary defense. As an additional layer, the endpoint also validates an `Origin` header check — requests must originate from `perfectday.andrewlass.com` or `perfectday-api.andrewlass.com`. Requests with an absent or mismatched Origin header (that are not from the same origin) are rejected with `403 forbidden_origin`. This defense-in-depth covers browsers that do not yet fully enforce SameSite=Strict.

## Object store access controls (Garage)

- Dedicated app service account. No public buckets.
- Non-guessable object keys: `{user_id}/{uuid}.enc`.
- No client-side read access. All reads proxied through the API.
- Uploads go through the API, which reads the body itself (`03-api-surface.md`, S-7); there are no signed PUT URLs. `perfectday-media.andrewlass.com` has a DNS record but no route. (The earlier plan, signed PUTs to a PUT-only media host, was dropped with S-7.)

## Backup

Celery beat task daily:

1. `pg_dump` is **encrypted with `age`** using a backup public key before it leaves the host. The corresponding private key is stored **separately from `master_secret`** — ideally on a different device or in a different secret store managed by a different operator. Without this separation, anyone who obtains both the backup bucket and `master_secret` can decrypt every historical photo and OAuth token.
2. (Superseded: backups are the nightly encrypted NAS copies of tracker row 36; see `deploy/nuc.md` § Backup.)

**Deleted data and backups.** A backup holds everything as it was that night, keys included, so a deleted account stays recoverable from older backups until they expire. Backups keep the newest 30 nights plus one per month for 3 months (Andrew, 2026-11-20), so deleted data leaves the backups within about 4 months. Removing one account from an existing backup is not done: each night is one encrypted bundle, and expiry is the lever. Restoring a night also brings back every account and diary deleted after it; until tracker row 67 makes the restore delete them again, the runbook (`deploy/nuc-ops.md` § B7) has a check by hand.

Backup destinations and operator-separation specifics are deployment concerns — see [`deploy/nuc.md`](../deploy/nuc.md) and `deploy/cloud.md`. This is irreplaceable family data.

## Subscription tier downgrade

When a user's tier is downgraded (by the operator via admin, or by expiry of a paid plan):

- Diaries over the new tier limit become **read-only**: scan disabled, new entries blocked, existing entries and photos still readable.
- Selection: diaries sorted by `created_at ASC` — the oldest diary stays active; newest over-limit diaries are downgraded.
- The user is shown a banner: "You have [N] diaries over your plan limit. Delete [N] diaries to restore scanning."
- No data is deleted on downgrade. The user can restore access by upgrading or by deleting diaries to get back under the limit.
- This behaviour is communicated in the plan-change confirmation screen before the downgrade is applied.

**Email delivery note (SendGrid free tier):** verify the current free-tier limit at account provisioning — historically 100 emails/day but terms change. If the limit is exhausted, consider Postmark (reliable deliverability) or AWS SES (~$0.10/1k emails). For a personal/family diary at PoC scale, 100/day is more than sufficient. Review annually.
