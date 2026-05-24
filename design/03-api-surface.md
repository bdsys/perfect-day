# API Surface

All endpoints prefixed `/v1/`. JSON. Auth via `Authorization: Bearer <jwt>` unless noted. Errors return `{error: {code, message, details?}}`. Pagination via `?cursor=&limit=`.

## Auth

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/auth/register` | Email + password registration. Returns access + refresh tokens. |
| POST | `/v1/auth/login` | Email + password login. |
| POST | `/v1/auth/social/google` | Exchange Google ID token for app JWTs. Auto-creates user on first login. |
| POST | `/v1/auth/social/facebook` | Same, for Facebook. |
| POST | `/v1/auth/social/apple` | Same, for Apple (required for iOS App Store). |
| POST | `/v1/auth/magic-link/request` | Send magic link email. |
| GET | `/v1/auth/magic-link/consume` | Exchanges token, sets refresh-token cookie, then `302`-redirects to a clean URL (token stripped from query string). The redirect target sets `Referrer-Policy: no-referrer`. `consumed_at` protects against retry leaks. |
| POST | `/v1/auth/refresh` | Rotate refresh token; return new access token. Reuse of a just-rotated token within a 30-second grace window is treated as a client retry (returns the same new token), not a theft signal. Reuse outside the window revokes the entire token family. Revocation rate is recorded for monitoring. |
| POST | `/v1/auth/logout` | Revoke current refresh token. |
| POST | `/v1/auth/logout-all` | Revoke all of the user's refresh tokens. |
| GET | `/v1/auth/me` | Current user profile, tier, OAuth scopes granted. |
| POST | `/v1/auth/password/forgot` | Send password reset email. |
| POST | `/v1/auth/password/reset` | Reset with email token. |
| POST | `/v1/auth/reauth` | Confirm password to elevate session for sensitive admin actions (15-min window). |
| DELETE | `/v1/auth/account` | Mark account for deletion; sets `hard_delete_after = now() + 7 days`. |
| POST | `/v1/auth/account/restore` | Cancel deletion within grace window. |

### Account linking rules

**Email-as-link-key (Google, Facebook):** when a social login arrives with an email that already exists on a different account, the API returns `409 link_required` instead of auto-linking. The frontend must guide the user through verification (existing-credentials login OR a verification email click-through) before `POST /v1/auth/social/{provider}/link` performs the merge. Auto-linking without verification enables account takeover.

**Apple Sign In (relay-aware):** Apple identities are keyed by `sub` (the durable `provider_user_id`), never by email. Apple Private Relay may rotate the relay address, and users can revoke it entirely. The relay address is stored separately in `social_identities.relay_email` for reference only and is never used as a lookup key. Apple users who sign in through multiple paths may end up with separate accounts; the design accepts this outcome rather than risking email-based takeover.

**`POST /v1/auth/social/{provider}/link`** — merges a verified social identity into an existing account. Requires active session of the target account; rejects if the `provider_user_id` is already linked to a different account.

**Deletion note:** Phase 1 ships `DELETE /v1/diaries/{id}` and `DELETE /v1/auth/account`. The `process_hard_deletes` Celery beat task is therefore promoted from Phase 2 to Phase 1 — see [09-poc-scope.md](09-poc-scope.md). Without it, soft-deleted rows accumulate indefinitely.

## OAuth integrations

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/integrations` | List connected providers, scopes granted, status. |
| GET | `/v1/integrations/google/authorize` | Returns OAuth consent URL with requested scopes. |
| GET | `/v1/integrations/google/callback` | OAuth redirect target. Persists tokens + `scopes_granted`. |
| POST | `/v1/integrations/google/refresh-scopes` | Re-request previously denied scopes (partial-grant recovery). |
| DELETE | `/v1/integrations/google` | Revoke. |
| GET/POST/DELETE | `/v1/integrations/spotify*` | Same shape (deferred, stub only). |

## Diaries

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/diaries` | Diaries the user owns or has access to. |
| POST | `/v1/diaries` | Create diary. Tier check (free=1 / tier1=2 / tier2=4). |
| GET | `/v1/diaries/{id}` | Details + permissions + scan config. |
| PATCH | `/v1/diaries/{id}` | Owner only. Patchable fields: `name`, `slug`, `subject_name`, `subject_relation`, `voice_override`, `tone_hint`, `timezone`, `scan_interval_minutes`, `scan_enabled`, `cover_photo_id` (must be a photo owned by the diary owner), `notifications_muted`. |
| DELETE | `/v1/diaries/{id}` | Soft delete + 30-day hard-delete schedule. Owner only. |
| POST | `/v1/diaries/{id}/restore` | Restore within grace window. |

## Diary sharing

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/diaries/{id}/permissions` | List members. Owner only. |
| POST | `/v1/diaries/{id}/invitations` | Invite by email. Owner only. |
| GET | `/v1/diaries/{id}/invitations` | List pending invitations. Owner only. |
| DELETE | `/v1/diaries/{id}/invitations/{invitationId}` | Revoke invite. Owner only. |
| POST | `/v1/invitations/{token}/accept` | Auth required. Creates `diary_permissions` row. |
| PATCH | `/v1/diaries/{id}/permissions/{userId}` | Change role. Owner only. |
| DELETE | `/v1/diaries/{id}/permissions/{userId}` | Remove member. Owner only or self. |

## Entries

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/diaries/{id}/entries` | Timeline. `?status=&from=&to=&cursor=&limit=`. Visibility filtered by role. |
| POST | `/v1/diaries/{id}/entries` | Manual entry. Tier check. |
| GET | `/v1/entries/{id}` | Full entry incl. events, photos, enrichments. |
| PATCH | `/v1/entries/{id}` | Edit title/body/dates/photos. Editor or owner. |
| POST | `/v1/entries/{id}/publish` | Draft → published. |
| POST | `/v1/entries/{id}/unpublish` | Back to draft. |
| DELETE | `/v1/entries/{id}` | Soft delete. |
| POST | `/v1/entries/{id}/restore` | Undo soft delete. |
| POST | `/v1/entries/{id}/regenerate` | Re-run LLM with current events. New `llm_generations` row. |
| POST | `/v1/entries/{id}/merge` | Reserved/optional for PoC. |
| POST | `/v1/entries/{id}/photos` | Attach photos. |
| DELETE | `/v1/entries/{id}/photos/{photoId}` | Detach. |

## Photos

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/photos` | User's photo library. Filters: `?diary_id=&from=&to=&attached=`. |
| POST | `/v1/photos/upload-url` | Pre-signed MinIO PUT URL + photo id. Client uploads directly. |
| POST | `/v1/photos/{id}/finalize` | Confirm upload. Worker reads EXIF, generates thumbnail. |
| GET | `/v1/photos/{id}` | Metadata + decrypt-and-stream download. |
| GET | `/v1/photos/{id}/thumbnail` | Decrypt-and-stream thumbnail. |
| DELETE | `/v1/photos/{id}` | Soft delete. |

Upload pattern: signed URLs to `media.diary.perfectday.andrewlass.com`. The edge proxy restricts to PUT only, rate-limited. Object keys non-guessable: `{user_id}/{uuid}.enc`. **Downloads always proxied through the API** — no MinIO signed URLs for reads.

Orphan sweeper runs every 6h via Celery beat; deletes `photos` rows where `finalized_at IS NULL AND created_at < now() - interval '24 hours'` plus the MinIO objects.

### Photo authorization

A caller may read photo P (full-size via `GET /v1/photos/{id}` or thumbnail via `GET /v1/photos/{id}/thumbnail`) iff one of the following is true:

- **(a) Owner:** `photo.user_id = caller.id`
- **(b) Shared viewer:** the photo is attached via `entry_photos` to an entry E where `E.status = 'published'`, `E.deleted_at IS NULL`, the diary is not soft-deleted, and the caller has any role (owner / editor / viewer) on that diary.
- **Draft entries:** only owners and editors of the diary may read photos attached exclusively to draft entries.

Photos attached to a deleted diary are inaccessible even to the photo owner until the cross-diary check confirms no other diary links them (in which case the photo row and MinIO object are deleted by `process_hard_deletes`).

## Scan worker

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/diaries/{id}/scan` | Scan config + last status. |
| POST | `/v1/diaries/{id}/scan/run` | On-demand scan now. Owner only. |
| GET | `/v1/diaries/{id}/scan/runs` | Scan history. |
| POST | `/v1/diaries/{id}/scan/backfill` | Queue backfill. `{from_date, to_date, sources}`. Owner only. |
| GET | `/v1/diaries/{id}/scan/backfill/{runId}` | Backfill job status. |
| DELETE | `/v1/diaries/{id}/scan/backfill/{runId}` | Cancel backfill. |

## Notifications

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/notifications/preferences` | Current preferences. |
| PATCH | `/v1/notifications/preferences` | Update push/email toggles, quiet hours. |
| POST | `/v1/notifications/devices` | Register Expo push token. |
| DELETE | `/v1/notifications/devices/{token}` | Unregister. |
| GET | `/v1/notifications` | In-app feed. |
| POST | `/v1/notifications/{id}/read` | Mark read. |

## Admin

Gated by `users.is_admin = true`. Destructive actions require recent re-auth (must have called `POST /v1/auth/reauth` within 15 minutes). All admin actions write to `audit_log`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/admin/users` | List with tier, last-login, deletion-pending. |
| GET | `/v1/admin/users/{id}` | Full user detail. |
| PATCH | `/v1/admin/users/{id}` | Override tier, mark email verified, force logout. |
| POST | `/v1/admin/users/{id}/impersonate` | Short-lived 1-hour token to view as user. Re-auth required. Writes audit_log. Sends in-app + email notification to the impersonated user: "An admin viewed your account on [timestamp] — contact support if unexpected." |
| DELETE | `/v1/admin/users/{id}` | Force account deletion (skips grace). Re-auth required. |
| GET | `/v1/admin/diaries` | All diaries across users. |
| GET | `/v1/admin/diaries/{id}` | Diary detail incl. owner + members. |
| DELETE | `/v1/admin/diaries/{id}` | Force delete. Re-auth required. |
| GET | `/v1/admin/audit-log` | Search audit log. |
| GET | `/v1/admin/scan-jobs` | All scan jobs with status. |
| POST | `/v1/admin/scan-jobs/{diaryId}/run` | Force scan, ignore backoff. |
| GET | `/v1/admin/llm-usage` | Token usage and cost by user/day. |
| GET | `/v1/admin/system/stats` | DB sizes, MinIO usage, queue depth. |

## Breadcrumb routes (deferred — add as commented router stubs)

```python
# v1/exports — see 09-poc-scope.md
# POST   /v1/diaries/{id}/export
# GET    /v1/exports/{jobId}

# v1/share — public OG-rendered shared entries
# GET    /v1/share/entry/{shareToken}    # public, SSR, noindex by default
# POST   /v1/entries/{id}/share          # mint or rotate token; revocable instantly
# DELETE /v1/entries/{id}/share

# v1/search — full-text over entries (pg_trgm or pgvector)
# GET    /v1/diaries/{id}/search?q=

# v1/reactions, v1/comments — explicit non-goals
```

## System / health

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness. |
| GET | `/readyz` | Readiness — Postgres, Redis, MinIO checks. |
| GET | `/metrics` | Prometheus exposition format. Bound to localhost or requires admin token. |
| GET | `/v1/admin/system/celery` | Admin-gated. Queue depth, worker count, oldest pending task. Re-auth not required. |

## Auth middleware checks

Every authenticated request runs middleware that:

1. Verifies the JWT signature and expiry. Returns `401 unauthorized` on failure.
2. Loads the `users` row. Returns `401 account_unavailable` if `users.deleted_at IS NOT NULL` or `users.hard_delete_after IS NOT NULL`. A valid access token issued before account deletion must not grant further access.
3. For diary-scoped endpoints: returns `404` if `diaries.deleted_at IS NOT NULL`. Callers without any role on the diary also get `404` (existence leak prevention — both cases look the same to the caller).

## Cross-cutting concerns

- **Tier enforcement:** every entry-create, diary-create, integration-toggle endpoint runs entitlement check. On block: HTTP 403 with `{error: {code: 'tier_limit', details: {limit, current, required_tier}}}`. Frontend disambiguates auth-failure / role-failure / tier-failure by `code`. Race condition mitigation: diary-create and entry-create use a per-user advisory lock in Postgres to prevent check-then-create races. For diary create (rare): acquire `pg_advisory_xact_lock(user_id)` within the transaction; count diaries and fail if at limit. For entry create (hot path): post-insert count verify with rollback if over limit (avoids holding a lock for LLM call duration).
- **Scan lock vs `/scan/run`:** `POST /v1/diaries/{id}/scan/run` returns `409 scan_in_progress` with a `Retry-After` header if the `scan_lock:{diary_id}` Redis lock is already held. Never silently skips — the caller always gets a clear signal.
- **Visibility/role enforcement:** owner / editor / viewer enforced on every diary-scoped endpoint. Viewers cannot see drafts. Editors cannot delete diary or manage permissions.
- **Idempotency:** manual entry creation and on-demand scan triggers accept `Idempotency-Key` header. Implementation: Redis 24h TTL keyed on `{idempotency_key}:{sha256(request_body)}`. On match: return the original response. On key match + body-hash mismatch: return `409 idempotency_conflict`.
- **API rate limiting:** per-user budget at FastAPI layer (100 req/min), independent of Google API quota handling in worker. Auth endpoints: 10 req/min per IP on login/register/magic-link.
- **Soft delete:** `DELETE` sets `deleted_at`. Hard-delete background jobs per security doc. `restore` endpoints work within grace.
- **Webhooks:** explicitly out of scope for PoC.
- **XSS on `body_markdown`:** server-side rendered via `unified` + `remark-parse` + `remark-rehype` + `rehype-sanitize` (GitHub schema). The frontend never uses `dangerouslySetInnerHTML` with raw HTML — only with the sanitized string produced by this pipeline. `body_markdown` is stored raw; sanitization happens at render time. This preserves the source-of-truth markdown as re-renderable while ensuring no script injection reaches the browser.
- **`email_verified_at` set when:** email/password = on confirmation link click; magic link = on first successful `GET /v1/auth/magic-link/consume`; Google = if provider's ID token contains `email_verified: true`; Facebook = if `email_verified` field is true in the Graph API response (treat as unverified if absent — Facebook does not guarantee this field); Apple = treat as verified per Apple TOS (Apple only grants access to the email when the account is verified).
- **Email change flow:** a user changing their email via `PATCH /v1/auth/me` must complete re-auth first. Both the old and new addresses receive confirmation emails: old address gets a "your email was changed — click to revert within 24h" email; new address gets a confirmation link. New email is not active until the new-address confirmation is clicked. `email_verified_at` is set to null until the new address is confirmed.
