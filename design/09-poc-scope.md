# PoC Scope Recommendation

## Goal

Prove the core loop end-to-end:

```
Sign up → connect Google Calendar → scan runs →
LLM generates draft → user reviews → user publishes
```

Everything else is either supporting infrastructure for this loop or a feature layer on top of it.

---

## Phase 1 — Foundation

Build in order. Nothing else works until this loop is proven.

1. **Postgres schema** — all tables from the data model + corrections. Alembic migrations from day one. No hand-created tables.
2. **FastAPI skeleton** — app factory, router structure, error handling, health/readiness endpoints, CORS (two-subdomain B2 topology), per-user rate limiting middleware.
3. **Auth: email+password + Google OAuth login** — `register`, `login`, `social/google`, `refresh`, `logout`. JWT + `refresh_tokens` table. Skip Facebook, Apple, magic link for this iteration.
4. **Diary + Entry CRUD** — create diary, list entries, create manual entry. No tier enforcement yet.
5. **Google Calendar grant** — `authorize` + `callback`, token storage, `oauth_tokens` AES-GCM encryption.
6. **Celery + Redis setup** — worker, beat, task infrastructure. Single `ping` test task to confirm pipeline.
7. **Scan worker: calendar only** — `scan_diary`, `ingest_calendar_event`, `group_events_into_entries` (single-day events only in Phase 1; multi-day in Phase 2).
8. **LLM draft generation** — `generate_entry_draft`, prompt builder, Anthropic API call, write draft to Entry. No Gemini fallback yet.
9. **Web UI: minimum viable diary view** — Next.js, two pages: diary timeline (entries list, draft/published status) and entry detail (read draft, edit body, publish button). No photos, no enrichments.
10. **Soft/hard delete flows** — `process_hard_deletes` Celery beat task, grace-period notifications, cascade deletion per security doc. Promoted from Phase 2 because Phase 1 ships the `DELETE /v1/diaries/{id}` and `DELETE /v1/auth/account` endpoints; without the background cleanup job, soft-deleted rows accumulate indefinitely and `hard_delete_after` is dead metadata.

**End of Phase 1:** Sign in → connect Calendar → watch a scan run → see a draft entry appear → edit → publish. That is the PoC.

---

## Phase 2 — Completeness

The "make the diary actually useful" phase. Photos, multi-day entries, weather, backfill, tier enforcement, LLM redundancy. Build in rough dependency order.

| # | Feature | Notes |
|---|---|---|
| 13 | **MinIO + photo upload** | `upload-url`, `finalize`, AES-GCM encryption, decrypt-and-stream download. |
| 14 | **Google Photos grant + scan** | Requires MinIO. Metadata-first filter, `ingest_photo`, `entry_photos` attachment. As part of this, extend backfill (item 17) to cover the `google_photos` source with the spec'd 90-day default cap. |
| 15 | **Multi-day entry support** | `entry_end_date`, multi-day grouping in worker, timeline display. |
| 16 | **Weather enrichment (Open-Meteo)** | No API key. Unlimited calls. Historical data going back decades — critical for backfill. Writes to `enrichments` table. As part of this, extend backfill (item 17) to enrich each chunk with weather. |
| 17 | **Backfill — slice 1 (Calendar-only, spec-compliant)** | Endpoint, `BackfillRun` table, and basic worker already exist. This slice fixes three gaps from the current implementation (see `design/06-scan-worker.md`): switch request body to `{from_date, to_date, sources}`, add weekly chunking with 2s sleep between chunks, acquire `scan_lock:{diary_id}` so backfill and regular scans serialize, and add `DELETE /v1/diaries/{id}/scan/backfill/{runId}` with chunk-boundary cancellation. Photos and weather sources are added with items 14 and 16, not here. |
| 18 | **Tier enforcement** | Entitlement checks on entry + diary creation. HTTP 403 with structured error. Upgrade prompt in UI. |
| 22 | **Gemini fallback for LLM** | Add after Anthropic integration is stable and tested. |

---

## Phase 3 — Auth, sharing, notifications, admin

Phase 2 leftovers grouped by theme. Build after Phase 2 is stable.

| # | Feature | Notes |
|---|---|---|
| 11 | **Apple Sign In + magic link** | Required before iOS App Store submission. |
| 12 | **Facebook OAuth login** | Low priority; after Apple. |
| 19 | **Diary sharing + invitations** | `diary_permissions`, `invitations`, accept flow, role-based visibility. |
| 20 | **Notifications** | Expo push + SendGrid, `notifications` table, dispatcher, quiet hours (20:00–07:00), coalescing, per-diary mute. |
| 21 | **Admin panel** | Web UI for admin endpoints: user CRUD (create/delete/list, tier changes), diary CRUD, entry CRUD, impersonate, force delete, LLM usage stats, scan fleet view. CLI data tools (`scripts/admin.sh`) are available now for local dev use. |

---

## Backlog

Not in PoC. Two groups: **deployment/infrastructure** that the PoC can ship without, and **product features** that need explicit user/business signal before being built. Leave breadcrumbs (schema columns, commented router stubs) where noted.

### Deployment / infrastructure

| Item | Notes |
|---|---|
| NUC deployment (bootstrap → secrets → first deploy → DDNS → FortiGate TLS → Google prod redirect URI → backups → smoke test) | Step-by-step in [`deploy/nuc-ops.md`](../deploy/nuc-ops.md). |
| Cloudflare DNS hand-off | Required before NUC deploy. |
| Third-party prod accounts: Google Cloud OAuth project, SendGrid relay, Anthropic prod key | Required before NUC deploy. |
| CD wiring (`DEPLOY_ENABLED`, `GHCR_TOKEN`, deploy SSH key) | Push-to-deploy automation; see `.github/workflows/deploy.yml` and `deploy/nuc-ops.md` § "CD wiring". |

### Product features (gated on user signal or later phase)

| Feature | Schema/code hook | How to add later |
|---|---|---|
| Vision LLM photo attribution (L3) | `photos.ai_description` column | Celery task calling Claude vision on flagged photos; gate behind paid tier |
| Spotify enrichment | Stub OAuth endpoints | Add full integration when Tier 2 is defined |
| Export (PDF/JPG/PNG) | Breadcrumb API routes | Add `render_export` Celery task |
| Social sharing (OG cards) | Breadcrumb API routes | Add `share_tokens` table + Next.js SSR share route |
| Expo mobile app | Same API surface | Start after Phase 2 web is stable |
| Stripe / billing | `users.stripe_customer_id` placeholder | Add Stripe webhooks + billing portal in v1.x |
| Data export (GDPR portability) | — | Add `GET /v1/auth/me/export` before public launch |
| Entry search (full-text) | Breadcrumb API route | `pg_trgm` or `pgvector` index on `entries.body_markdown` |
| Comments / reactions | Breadcrumb API routes | Explicit non-goals; add if user research warrants |
| Photo-only auto entries | Worker logic only | Minor change once Photos integration is stable |
| Auto-backfill on first OAuth connect | Worker logic | Not in `design/06-scan-worker.md`. Today the first scan only pulls 90 days back; for the "diary of a child's life" use case we may want to offer (or auto-run) a longer initial backfill. Needs a design decision before building. |

---

## Resource estimates

Single-host resource estimates and the Celery-concurrency cap are deployment-target dependent — see [`deploy/nuc.md`](../deploy/nuc.md).

> **Hybrid deployment timing:** the hybrid topology (NUC + Hetzner CX21 cloud edge) is a Phase 1.5 / Phase 2 deployment switch — designed now, adopted later. The Phase 1 PoC builds NUC-only per the scope above. The host-agnostic architecture (`design/01-architecture.md` § Deployment targets) supports the hybrid switch without application code rewrites. See [`deploy/hybrid.md`](../deploy/hybrid.md) for the full hybrid design.
