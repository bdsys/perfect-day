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

**End of Phase 1:** Sign in → connect Calendar → watch a scan run → see a draft entry appear → edit → publish. That is the PoC.

---

## Phase 2 — Completeness

After Phase 1 works end-to-end. In rough dependency order:

| # | Feature | Notes |
|---|---|---|
| 10 | **Apple Sign In + magic link** | Required before iOS App Store submission. |
| 11 | **Facebook OAuth login** | Low priority; after Apple. |
| 12 | **MinIO + photo upload** | `upload-url`, `finalize`, AES-GCM encryption, decrypt-and-stream download. |
| 13 | **Google Photos grant + scan** | Requires MinIO. Metadata-first filter, `ingest_photo`, `entry_photos` attachment. |
| 14 | **Multi-day entry support** | `entry_end_date`, multi-day grouping in worker, timeline display. |
| 15 | **Weather enrichment (Open-Meteo)** | No API key. Unlimited calls. Historical data going back decades — critical for backfill. Writes to `enrichments` table. |
| 16 | **Backfill** | `POST /scan/backfill`, `backfill_runs`, chunked weekly scan, cancellation. |
| 17 | **Tier enforcement** | Entitlement checks on entry + diary creation. HTTP 403 with structured error. Upgrade prompt in UI. |
| 18 | **Diary sharing + invitations** | `diary_permissions`, `invitations`, accept flow, role-based visibility. |
| 19 | **Notifications** | Expo push + SendGrid, `notifications` table, dispatcher, quiet hours (20:00–07:00), coalescing, per-diary mute. |
| 20 | **Admin panel** | Web UI for admin endpoints: impersonate, force delete, LLM usage, scan fleet view. |
| 21 | **Soft/hard delete flows** | `process_hard_deletes` Celery beat task, grace-period notifications, cascade deletion. |
| 22 | **Gemini fallback for LLM** | Add after Anthropic integration is stable and tested. |

---

## Phase 3 — Deferred

Not in PoC. Leave breadcrumbs (schema columns, commented router stubs) as noted.

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

---

## NUC resource estimates

| Service | Idle RAM |
|---|---|
| PostgreSQL | 100–200 MB |
| Redis | 70–150 MB |
| FastAPI (2 workers) | 100–150 MB |
| Celery worker (2 processes) | 150–250 MB |
| Celery beat | 50 MB |
| MinIO | 100–200 MB |
| Next.js SSR (Node) | 150–300 MB |
| **Total** | **~720 MB – 1.3 GB** |

Comfortable on 8 GB. Risk zone: heavy backfill + LLM calls simultaneously → CPU saturation before RAM. Mitigation: cap Celery worker concurrency at 2 for PoC.

Fallback if RAM becomes a problem: switch Celery workers to a single-process async worker (`arq`). Task interface nearly identical; cuts worker RAM ~50%.
