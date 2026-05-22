# Design Review — Open Items

Tracks issues from the design review pass. Updated 2026-05-22 after full design completion pass.

---

## Critical issues — all closed

- [x] **C1** — Master secret colocated with ciphertext → `08-security-privacy.md`: `master_secret` now loaded from secret store (sops+YubiKey for NUC, managed secret manager for cloud) *(patched 2026-05-21)*
- [x] **C2** — Backup is master compromise vector → `08-security-privacy.md`: `pg_dump` now `age`-encrypted with a separate backup key before upload *(patched 2026-05-21)*
- [x] **C3** — AES-256-GCM not safely streamable for large photos → `08-security-privacy.md`: replaced whole-blob with chunked 1 MiB AES-256-GCM encryption and streaming verification *(patched 2026-05-21)*
- [x] **C4** — Soft-deleted users still authenticate → `03-api-surface.md`: auth middleware now rejects `users.deleted_at IS NOT NULL` / `hard_delete_after IS NOT NULL` *(patched 2026-05-21)*
- [x] **C5** — Photo authorization undefined → `03-api-surface.md`: explicit § Photo authorization rule; `02-data-model.md`: cross-ref added *(patched 2026-05-21)*
- [x] **C6** — Refresh-token family revocation too aggressive in normal use → `03-api-surface.md`: 30-second reuse grace window documented *(patched 2026-05-21)*
- [x] **C7** — Scan lock TTL shorter than scan duration → `06-scan-worker.md`: TTL raised to 30 min + heartbeat renewal every 5 min *(patched 2026-05-21)*
- [x] **C8** — Token-refresh race self-revokes live integration → `06-scan-worker.md`: per-`(user_id, provider)` Redis advisory lock on token refresh *(patched 2026-05-21)*
- [x] **C9** — Email-based account linking enables takeover → `03-api-surface.md`, `05-google-oauth-integrations.md`: auto-linking removed; `link_required` flow added *(patched 2026-05-21)*
- [x] **C10** — Magic-link tokens leak via Referer → `03-api-surface.md`: consume endpoint redirects to clean URL; `Referrer-Policy: no-referrer` *(patched 2026-05-21)*
- [x] **C11** — Apple Private Relay breaks email-based account linking → `03-api-surface.md`, `05-google-oauth-integrations.md`: Apple keyed by `sub`; relay email stored separately *(patched 2026-05-21)*
- [x] **C12** — Prompt injection via calendar-event titles → `04-llm-integration.md`: `<event>` delimiters, role-token stripping, citation validator *(patched 2026-05-21)*
- [x] **C13** — XSS strategy for `body_markdown` undefined → `03-api-surface.md` § Cross-cutting: `unified` + `rehype-sanitize` GitHub schema; no `dangerouslySetInnerHTML` raw HTML *(patched 2026-05-22 as M26)*
- [x] **C14** — `entry_date` timezone ambiguous → `06-scan-worker.md` § Time and timezones; `design/time-and-tz.md` authored *(patched 2026-05-21 / 2026-05-22)*
- [x] **C15** — Phase 1 delete endpoints with no cleanup job → `09-poc-scope.md`: `process_hard_deletes` promoted from Phase 2 to Phase 1 item 10 *(patched 2026-05-21)*

---

## Major issues — closed (patched 2026-05-22)

- [x] **M1** — Tier enforcement check-then-create race → `03-api-surface.md` § Cross-cutting: advisory lock (diary create) + post-insert verify-and-rollback (entry create)
- [x] **M2** — Notification coalescing read-then-write race → `07-notifications.md` § Coalescing: Redis SETNX atomic coalescing key
- [x] **M3** — Quiet-hours release storm → `07-notifications.md` § Quiet hours: 0–15 min jitter + coalesce again at release
- [x] **M4** — `entry_edit_diffs` PoC limitation → `02-data-model.md` § Behavior decisions: documented as known limitation; revisit post-launch
- [x] **M5** — Photo grouping non-deterministic on tie → `06-scan-worker.md`: total order `entry_date ASC, created_at ASC, id ASC` defined
- [x] **M6** — `upsert_entry` reuse logic under-specified → `06-scan-worker.md`: all 3 cases specified (new draft / reuse draft / reject published)
- [x] **M7** — Scan lock vs `/scan/run` conflict UX → `03-api-surface.md` § Cross-cutting: `409 scan_in_progress` with `Retry-After`
- [x] **M8** — Backfill cancel semantics → `06-scan-worker.md`: "stop after current week" explicitly documented
- [x] **M9** — `notifications` table unbounded → `02-data-model.md` § Behavior decisions: 90-day retention Celery beat task
- [x] **M10** — Retention horizons undocumented → `02-data-model.md` § Behavior decisions: 1yr audit, 90d scan_runs, 1yr llm_generations
- [x] **M11** — `/healthz`/`/readyz` insufficient → `03-api-surface.md` § System/health: `/metrics` endpoint + `/v1/admin/system/celery`
- [x] **M12** — CORS dev-tunnel footgun → `01-architecture.md` § Decisions locked: dev tunnel gated on `ENV=development` only
- [x] **M13** — No CSRF on cookie-based refresh → `08-security-privacy.md` § CSRF: Origin header check + SameSite=Strict double defense
- [x] **M14** — Prompt cache cost estimate unverified → `04-llm-integration.md` § Model choice: "verify with real prompt sizes before tier pricing" caveat added
- [x] **M15** — Title fact-citation missing → closed under C12 patch (`title_facts_used` added)
- [x] **M16** — Open-Meteo re-fetch on backfill → `06-scan-worker.md` § Photo handling edge cases: `enrichments` check before fetch
- [x] **M17** — Slug global uniqueness leaks existence → `02-data-model.md` § Diaries: changed to `UNIQUE(owner_user_id, slug)` with explanation
- [x] **M18** — Photo metadata filter rejects EXIF-stripped uploads → `06-scan-worker.md` § Photo handling edge cases: user-uploads bypass filter; Google Photos auto-ingest applies it
- [x] **M19** — Cover-photo selection unspecified → `03-api-surface.md` § Diaries PATCH: `cover_photo_id` listed in patchable fields with validation note
- [x] **M20** — Admin impersonation no time bound → `03-api-surface.md` § Admin table: 1-hour cap, audit log, user notification
- [x] **M21** — Email change no verification flow → `03-api-surface.md` § Cross-cutting: re-auth + old-address revert link + new-address confirmation
- [x] **M22** — No share-token revocation — **deferred to Phase 3** share feature. Track when implemented.
- [x] **M23** — Idempotency-Key spec missing → `03-api-surface.md` § Cross-cutting: Redis 24h TTL, body-hash, 409 on mismatch
- [x] **M24** — CI/CD not documented → `design/ci-cd.md` authored
- [x] **M25** — `email_verified_at` per-provider rules → `03-api-surface.md` § Cross-cutting: per-provider rules documented (including Facebook ambiguity)
- [x] **M26 / C13** — XSS on `body_markdown` → `03-api-surface.md` § Cross-cutting: `unified` + `rehype-sanitize` GitHub schema

---

## Minor issues — closed (patched 2026-05-22)

- [x] **m1** — `oauth_tokens.provider` enum — `facebook` already excluded. No change needed.
- [x] **m2** — `social_identities.provider` enum — `apple` already included. No change needed.
- [x] **m3** — `entries.status` includes `archived` → `02-data-model.md`: `archived` removed from enum. Two states only: `draft | published`.
- [x] **m4** — `notifications.kind` payload schemas → `07-notifications.md` § Notification payload schemas: full payload shape per kind
- [x] **m5** — `scan_runs.errors jsonb` has no schema → `02-data-model.md`: schema `[{source, error_class, message, retried_count}]` documented
- [x] **m6** — `magic_link_tokens.email` not lowercased → `08-security-privacy.md` § Magic link tokens: `LOWER(email)` on insert documented
- [x] **m7** — `enrichments.source` had `openweather` → `02-data-model.md`: changed to `open_meteo`
- [x] **m8** — `events.source` had `weather` → `02-data-model.md`: `weather` removed (weather is an enrichment, not an event)
- [x] **m9** — `photos.s3_key UNIQUE` + orphan sweeper — UUIDs are unique per upload-url call; safe. One-line note in upload-flow spec.
- [x] **m10** — `refresh_tokens.device_hint` set at login not refresh → `02-data-model.md`: clarified in field comment
- [x] **m11** — Cost estimate unverified — closed under M14.
- [x] **m12** — `photos_backfill_days_max` should be tier config → `02-data-model.md`: noted as "Phase 2 will move to tier config"
- [x] **m13** — SendGrid free-tier terms → `08-security-privacy.md` § Email delivery note: "verify at provisioning" + fallback options
- [x] **m14** — `dispatch_due_scans()` 65-min worst-case latency → `06-scan-worker.md` § Beat dispatch: documented
- [x] **m15** — Subscription tier downgrade spec → `08-security-privacy.md` § Subscription tier downgrade + `02-data-model.md` § Behavior decisions

---

## Deferred (track at Phase 3 implementation)

- **M22** — Share-token revocation: `DELETE /v1/entries/{id}/share` revokes the published-entry share token. Spec when Phase 3 share feature is designed.
