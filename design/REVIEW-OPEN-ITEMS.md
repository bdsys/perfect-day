# Design Review — Open Items

Tracks issues from the design review pass. Critical issues (C1–C15) are patched; Major (M) and Minor (m) issues remain open.

---

## Critical issues — closed (patched 2026-05-21)

- [x] **C1** — Master secret colocated with ciphertext → `08-security-privacy.md`: `master_secret` now loaded from secret store (sops+YubiKey for NUC, managed secret manager for cloud)
- [x] **C2** — Backup is master compromise vector → `08-security-privacy.md`: `pg_dump` now `age`-encrypted with a separate backup key before upload
- [x] **C3** — AES-256-GCM not safely streamable for large photos → `08-security-privacy.md`: replaced whole-blob with chunked 1 MiB AES-256-GCM encryption and streaming verification
- [x] **C4** — Soft-deleted users still authenticate → `03-api-surface.md`: auth middleware now rejects `users.deleted_at IS NOT NULL` / `hard_delete_after IS NOT NULL`
- [x] **C5** — Photo authorization undefined → `03-api-surface.md`: explicit § Photo authorization rule; `02-data-model.md`: cross-ref added
- [x] **C6** — Refresh-token family revocation too aggressive in normal use → `03-api-surface.md`: 30-second reuse grace window documented
- [x] **C7** — Scan lock TTL shorter than scan duration → `06-scan-worker.md`: TTL raised to 30 min + heartbeat renewal every 5 min
- [x] **C8** — Token-refresh race self-revokes live integration → `06-scan-worker.md`: per-`(user_id, provider)` Redis advisory lock on token refresh
- [x] **C9** — Email-based account linking enables takeover → `03-api-surface.md`, `05-google-oauth-integrations.md`: auto-linking removed; `link_required` flow added
- [x] **C10** — Magic-link tokens leak via Referer → `03-api-surface.md`: consume endpoint redirects to clean URL; `Referrer-Policy: no-referrer`
- [x] **C11** — Apple Private Relay breaks email-based account linking → `03-api-surface.md`, `05-google-oauth-integrations.md`: Apple keyed by `sub`; relay email stored separately; `02-data-model.md`: `social_identities.relay_email citext NULL` added
- [x] **C12** — Prompt injection via calendar-event titles → `04-llm-integration.md`: `<event>` delimiters, role-token stripping on ingest, citation validator defense documented
- [x] **C13** — XSS strategy for `body_markdown` undefined → **NOT YET PATCHED** — needs a design decision (server-side `markdown-it` + DOMPurify allowlist vs. `unified` + `rehype-sanitize`). Tracked as M26 below.
- [x] **C14** — `entry_date` timezone ambiguous → `06-scan-worker.md`: § Time and timezones added; `02-data-model.md`: timezone notes on `diaries.timezone` and `entries.entry_date`
- [x] **C15** — Phase 1 delete endpoints with no cleanup job → `09-poc-scope.md`: `process_hard_deletes` promoted from Phase 2 to Phase 1 item 10

> **Note on C13:** The fix involves a frontend rendering decision not yet made. Opened as M26 below.

---

## Major issues — open

- [ ] **M1** — Tier enforcement check-then-create race (file: `03-api-surface.md` + worker; fix: advisory lock per diary on entry create, or post-insert verify-and-rollback)
- [ ] **M2** — Notification coalescing has a read-then-write race (file: `07-notifications.md`; fix: Redis SETNX coalescing key with TTL)
- [ ] **M3** — Quiet-hours release storm (file: `07-notifications.md`; fix: jitter eta by 0–15 min, coalesce again at release)
- [ ] **M4** — `entry_edit_diffs` captures only last-regen-to-published diff (file: `02-data-model.md`; fix: store all generations or document the limitation explicitly)
- [ ] **M5** — Photo grouping is non-deterministic on tie (file: `06-scan-worker.md`; fix: define total order `entry_date ASC, created_at ASC, id ASC`)
- [ ] **M6** — `upsert_entry` reuse logic under-specified for published entries (file: `06-scan-worker.md`; fix: specify all 3 cases: new draft / attach to published / reject)
- [ ] **M7** — Scan lock + manual `/scan/run` UX on conflict undefined (file: `03-api-surface.md`; fix: define whether silent skip, 409, or queued)
- [ ] **M8** — Backfill cancel leaves partial weeks done (file: `06-scan-worker.md`; fix: document "stop after current week" semantics explicitly)
- [ ] **M9** — `notifications` table grows unbounded (file: `02-data-model.md`; fix: Celery beat task deletes `created_at < now() - 90d AND read_at IS NOT NULL`)
- [ ] **M10** — `audit_log`, `scan_runs`, `llm_generations` have no retention (file: `02-data-model.md`; fix: document retention horizons — 1yr audit, 90d scan_runs, 1yr llm_generations)
- [ ] **M11** — `/healthz`/`/readyz` not enough for ops (file: `03-api-surface.md`; fix: `celery_inspect` output behind admin auth, metrics endpoint)
- [ ] **M12** — CORS allowlist for Expo dev tunnel is footgun-shaped (file: `01-architecture.md`; fix: dev tunnel allowed only when `ENV=dev`; production is exact-match — noted in `deploy/nuc.md` but not enforced in code spec)
- [ ] **M13** — No CSRF protection on cookie-based refresh endpoint (file: `08-security-privacy.md`; fix: double-submit CSRF token on `/v1/auth/refresh` or Origin header check)
- [ ] **M14** — Anthropic prompt cache hit rate overestimated (file: `04-llm-integration.md`; fix: re-verify cost estimates with real prompt sizes before tier pricing)
- [ ] **M15** — No fact-citation enforcement for title field → **Closed in C12 patch** (title_facts_used added to output schema)
- [ ] **M16** — Open-Meteo backfill rate-limits exist (file: `06-scan-worker.md`; fix: verify worker checks `enrichments` table before fetching weather for a date already enriched)
- [ ] **M17** — Slug collisions across users leak diary existence (file: `02-data-model.md`; fix: scope UNIQUE to `(owner_user_id, slug)` not global; update display URL structure)
- [ ] **M18** — Photo metadata filter rejects EXIF-stripped photos (file: `06-scan-worker.md`; fix: user-uploaded photos bypass the filter; Google Photos auto-ingest applies it)
- [ ] **M19** — No spec for cover-photo selection (file: `03-api-surface.md`; fix: confirm `cover_photo_id` is settable via `PATCH /v1/diaries/{id}` and add it to the field list)
- [ ] **M20** — Admin impersonation has no time bound (file: `03-api-surface.md`; fix: 1-hour cap, audit log per use, impersonated user notified in-app + email)
- [ ] **M21** — Notification preferences have no "verify before changing email" flow (file: `03-api-surface.md`; fix: email change requires re-auth; old email gets "your address was changed" with revert link)
- [ ] **M22** — No way to revoke a published-entry share token (deferred to Phase 3 share feature; track when implemented)
- [ ] **M23** — Idempotency-Key implementation unspecified (file: `03-api-surface.md`; fix: Redis 24h TTL, return original response on key match, 409 on key + different body hash)
- [ ] **M24** — CI/CD not documented (file: missing; fix: add `design/ci-cd.md`)
- [ ] **M25** — `users.email_verified_at` set when? (file: `03-api-surface.md` + `05`; fix: document verification flow for each auth provider: email/pass, magic link, Google, Facebook, Apple)
- [ ] **M26** — XSS strategy for `body_markdown` undefined (from C13; file: `03-api-surface.md` + Next.js code; fix: server-side render with `unified` + `rehype-sanitize` GitHub schema; never `dangerouslySetInnerHTML` raw HTML)

---

## Minor issues — open

- [ ] **m1** — `oauth_tokens.provider` enum — `facebook` was already excluded from the file. No change needed. ✅
- [ ] **m2** — `social_identities.provider` enum — `apple` already included. ✅
- [ ] **m3** — `entries.status` includes `archived` but no endpoint sets it (file: `03-api-surface.md`; fix: add endpoint or remove `archived` from enum)
- [ ] **m4** — `notifications.kind` enum / payload fields for `tier_limit` unspecified (file: `07-notifications.md`; fix: document payload shape for each kind)
- [ ] **m5** — `scan_runs.errors jsonb` has no schema (file: `02-data-model.md`; fix: `[{source, error_class, message, retried_count}]`)
- [ ] **m6** — `magic_link_tokens.email` should be `lowercase()`d on insert (citext handles comparison but not normalization on display)
- [ ] **m7** — `enrichments.source` includes `openweather` but design picks Open-Meteo (file: `02-data-model.md`; fix: use `open_meteo`)
- [ ] **m8** — `events.source` should not include `weather` (weather is an enrichment, not an event)
- [ ] **m9** — `photos.s3_key UNIQUE` + orphan sweeper + retried upload: UUIDs are unique per `POST /v1/photos/upload-url` call so this is safe, but worth noting in the upload flow spec
- [ ] **m10** — `refresh_tokens.device_hint` set from the request that created the token (clarify in `03-api-surface.md` — it's set at login/register time, not at refresh time)
- [ ] **m11** — Cost estimate `$0.005–$0.015` on Sonnet assumes ~500 input tokens/entry — verify with a real prompt before relying on it for tier pricing (also M14)
- [ ] **m12** — `photos_backfill_days_max` is on `diaries` but the limit is really per-user-tier (file: `02-data-model.md`; consider moving to user tier config)
- [ ] **m13** — SendGrid free tier: verify current terms (currently 100 emails/day for 30 days then zero, not indefinitely 100/day)
- [ ] **m14** — `dispatch_due_scans()` every 5 min means worst-case dispatch latency for 60-min scan is 65 min — acceptable but document
- [ ] **m15** — No spec for `subscription_tier` downgrade mid-cycle (extra diaries become read-only? Hidden?) — title citation in output schema closed under C12 patch ✅
