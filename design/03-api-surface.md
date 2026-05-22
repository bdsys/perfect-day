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
| GET | `/v1/auth/magic-link/consume` | Consume token from magic link URL; returns JWTs. |
| POST | `/v1/auth/refresh` | Rotate refresh token; return new access token. |
| POST | `/v1/auth/logout` | Revoke current refresh token. |
| POST | `/v1/auth/logout-all` | Revoke all of the user's refresh tokens. |
| GET | `/v1/auth/me` | Current user profile, tier, OAuth scopes granted. |
| POST | `/v1/auth/password/forgot` | Send password reset email. |
| POST | `/v1/auth/password/reset` | Reset with email token. |
| POST | `/v1/auth/reauth` | Confirm password to elevate session for sensitive admin actions (15-min window). |
| DELETE | `/v1/auth/account` | Mark account for deletion; sets `hard_delete_after = now() + 7 days`. |
| POST | `/v1/auth/account/restore` | Cancel deletion within grace window. |

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
| PATCH | `/v1/diaries/{id}` | Owner only. |
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

Upload pattern: signed URLs to `media.diary.perfectday.bdsys.net`. FortiGate WAF restricts to PUT only, rate-limited. Object keys non-guessable: `{user_id}/{uuid}.enc`. **Downloads always proxied through the API** — no MinIO signed URLs for reads.

Orphan sweeper runs every 6h via Celery beat; deletes `photos` rows where `finalized_at IS NULL AND created_at < now() - interval '24 hours'` plus the MinIO objects.

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
| POST | `/v1/admin/users/{id}/impersonate` | Short-lived token to view as user. Re-auth required. |
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

## Cross-cutting concerns

- **Tier enforcement:** every entry-create, diary-create, integration-toggle endpoint runs entitlement check. On block: HTTP 403 with `{error: {code: 'tier_limit', details: {limit, current, required_tier}}}`. Frontend disambiguates auth-failure / role-failure / tier-failure by `code`.
- **Visibility/role enforcement:** owner / editor / viewer enforced on every diary-scoped endpoint. Viewers cannot see drafts. Editors cannot delete diary or manage permissions.
- **Idempotency:** manual entry creation and on-demand scan triggers accept `Idempotency-Key` header.
- **API rate limiting:** per-user budget at FastAPI layer (100 req/min), independent of Google API quota handling in worker. Auth endpoints: 10 req/min per IP on login/register/magic-link.
- **Soft delete:** `DELETE` sets `deleted_at`. Hard-delete background jobs per security doc. `restore` endpoints work within grace.
- **Webhooks:** explicitly out of scope for PoC.
