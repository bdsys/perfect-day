# Security & Privacy Design

## Photo encryption at rest

Two-layer key hierarchy, app-level. All downloads proxied through the API (no MinIO signed URLs for reads).

```
master_secret   = env var (32-byte random, set at deploy time)
KEK for user U  = HKDF-SHA256(master_secret, salt=user_id, info="photo-kek")
DEK for photo P = random 32 bytes generated at upload time

On ingest:
  ciphertext = AES-256-GCM(DEK, photo_bytes)
  wrapped_DEK = AES-256-GCM(KEK, DEK)  [nonce prepended]
  MinIO: store ciphertext at {user_id}/{photo_uuid}.enc
  Postgres: photos.dek_ciphertext = [key_version_byte || nonce || wrapped_DEK]

On download:
  GET /v1/photos/{id}
  → backend retrieves ciphertext from MinIO
  → unwraps DEK with KEK
  → decrypts
  → streams plaintext bytes to client
```

- Signed MinIO URLs used only for uploads (direct client-to-MinIO PUT). Downloads always through the API.
- Thumbnails encrypted with the same DEK; served via `/v1/photos/{id}/thumbnail` with same decrypt-and-stream path.
- Key version prefix in `dek_ciphertext` supports future master_secret rotation (re-wrap DEKs in Postgres; ciphertext in MinIO unchanged).
- OAuth tokens use a **separate** master secret env var and the same AES-256-GCM pattern.

## JWT lifecycle

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
           Photos deleted from MinIO + photos table only if no other diary
           links them (cross-diary check via diary_photos).
           Hard delete diaries row. Write audit_log.
```

### Account deletion (7-day grace)

```
Day 0:   deleted_at set, hard_delete_after = now() + 7d.
         Immediately: revoke all refresh_tokens + oauth_tokens.
         Disable all scans. Confirmation email + restore link sent.
         Shared-diary members notified: "export your content now."
Day 6:   deletion_grace notification (last chance).
Day 7:   Celery process_hard_deletes():
           Hard delete all owned diaries (cascade per diary flow above).
           Delete notifications, notification_preferences, social_identities,
           oauth_tokens, magic_link_tokens, refresh_tokens.
           Scrub MinIO: delete everything under {user_id}/* prefix.
           Anonymize audit_log rows (null user_id, preserve action/timestamp).
           Hard delete users row.
```

Cross-diary photo check: `photos` row and MinIO object deleted only when the last linking `diary_photos` row is gone.

## GDPR/CCPA posture

| Item | PoC status | Pre-public action required |
|---|---|---|
| Right to erasure | ✅ Covered by deletion flow | — |
| Right to access/portability | ❌ Not built | Add `GET /v1/auth/me/export` (JSON + photo ZIP) |
| Consent for photo processing | ❌ Not built | Explicit consent UI; document lawful basis |
| Children's data (COPPA/GDPR Art.8) | ℹ️ Parent-operator model; child is not a user | Legal review before public launch |
| Privacy policy | ❌ Not written | Required before public launch |
| DPAs with vendors | ❌ Not executed | Anthropic, Google, SendGrid, Expo each need a DPA for GDPR |
| Data residency | ℹ️ NUC only for PoC | EU public launch needs EU data residency |
| Breach notification | ❌ No runbook | 72-hour GDPR requirement; runbook + contact email needed |

## Auth endpoints rate limiting

- `/auth/login`, `/auth/register`, `/auth/magic-link/request`: 10 req/min per IP.
- Magic link: max 3 requests per email per 10 minutes.

## Magic link tokens

32-byte random (URL-safe base64). Hash-only storage in `magic_link_tokens`. 15-min TTL. Single-use (`consumed_at` set on first use).

## CSRF

`HttpOnly SameSite=Strict` cookie on refresh tokens. Custom `Authorization` header on API calls (not form-submittable). No additional CSRF token needed.

## MinIO access controls

- Dedicated app service account. No public buckets.
- Non-guessable object keys: `{user_id}/{uuid}.enc`.
- No client-side read access. All reads proxied through the API.
- Uploads go to `media.diary.perfectday.bdsys.net` via signed PUT URLs; FortiGate WAF restricts to PUT only.

## Backup

Celery beat task daily: `pg_dump` → MinIO (local) + sync to external cloud bucket (S3 or Backblaze B2). Cost ~$5/mo. Protects against NUC disk failure. Required — this is irreplaceable family data.
