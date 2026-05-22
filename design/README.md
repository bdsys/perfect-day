# Perfect Day — Design Documents

All 10 planning deliverables for the PoC. These are authoritative — use them as the implementation specification.

| File | Topic |
|---|---|
| [01-architecture.md](01-architecture.md) | Component diagram, service topology, flow notes |
| [02-data-model.md](02-data-model.md) | Full Postgres schema (all tables + corrections from later deliverables) |
| [03-api-surface.md](03-api-surface.md) | All FastAPI endpoints, cross-cutting concerns, breadcrumb stubs |
| [04-llm-integration.md](04-llm-integration.md) | Prompt structure, voice derivation, anti-hallucination, failure handling |
| [05-google-oauth-integrations.md](05-google-oauth-integrations.md) | Auth providers, Calendar/Photos grant flow, partial-grant handling |
| [06-scan-worker.md](06-scan-worker.md) | Celery beat schedule, scan loop, grouping algorithm, backfill, rate limits |
| [07-notifications.md](07-notifications.md) | Notification kinds, channels (Expo push + SendGrid), coalescing, quiet hours |
| [08-security-privacy.md](08-security-privacy.md) | Photo encryption, JWT lifecycle, deletion flows, GDPR posture |
| [09-poc-scope.md](09-poc-scope.md) | Phase 1 / 2 / 3 build order, NUC resource estimates |
| [10-open-questions.md](10-open-questions.md) | All OQ-1 through OQ-11 resolved decisions |

---

## Repo layout (OQ-8)

```
perfect-day/
  apps/
    api/          (FastAPI — Python)
    web/          (Next.js — TypeScript)
    mobile/       (Expo — TypeScript, Phase 2)
  packages/
    api-types/    (openapi-typescript generated; shared by web + mobile)
  design/         (these files)
  docker-compose.yml
  docker-compose.dev.yml
```

`pnpm workspaces` for PoC. Add Turborepo if build times become painful.

---

## Phase 1 build order (quick reference)

Build in order — nothing else works until this loop is proven:

1. **Postgres schema** — all tables + Alembic migrations from day one. No hand-created tables.
2. **FastAPI skeleton** — app factory, routers, error handling, health/readiness endpoints, CORS, rate limiting middleware.
3. **Auth: email+password + Google login** — `register`, `login`, `social/google`, `refresh`, `logout`. JWT + `refresh_tokens`. Skip Facebook, Apple, magic link for now.
4. **Diary + Entry CRUD** — create diary, list entries, create manual entry. No tier enforcement yet.
5. **Google Calendar grant** — `authorize` + `callback`, token storage, AES-GCM encryption.
6. **Celery + Redis setup** — worker, beat, task infrastructure. Single `ping` test task.
7. **Scan worker: calendar only** — `scan_diary`, `ingest_calendar_event`, `group_events_into_entries` (single-day events only).
8. **LLM draft generation** — `generate_entry_draft`, prompt builder, Anthropic API call, write draft to Entry.
9. **Web UI: minimum viable diary view** — Next.js, two pages: timeline (draft/published) and entry detail (read/edit/publish).

**End of Phase 1:** Sign in → connect Calendar → scan runs → draft appears → edit → publish.
