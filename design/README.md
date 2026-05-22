# Perfect Day — Design Documents

All planning deliverables for the PoC. These are authoritative — use them as the implementation specification.

## Core design (10 docs)

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
| [09-poc-scope.md](09-poc-scope.md) | Phase 1 / 2 / 3 build order; resource estimates in `deploy/nuc.md` |
| [10-open-questions.md](10-open-questions.md) | All OQ-1 through OQ-11 resolved decisions |

## Topic docs (6 docs)

| File | Topic |
|---|---|
| [time-and-tz.md](time-and-tz.md) | Authoritative timezone rules, DST handling, worker date conventions |
| [secrets.md](secrets.md) | Secret inventory, storage backends, rotation procedures, compromise response |
| [dns-and-email.md](dns-and-email.md) | DNS topology, SPF/DKIM/DMARC setup, SendGrid sender identity |
| [observability.md](observability.md) | Logs/metrics/alerts stack (Sentry + Grafana Cloud + Better Stack) |
| [testing.md](testing.md) | Test pyramid, mocking policy, fixtures, coverage targets, CI integration |
| [ci-cd.md](ci-cd.md) | GitHub Actions pipelines, image registry, NUC deploy procedure |

## Deployment docs (3 docs)

| File | Topic |
|---|---|
| [../deploy/nuc.md](../deploy/nuc.md) | Single-host home-lab (Intel NUC): resource budget, edge config, backup, SPoF |
| [../deploy/cloud.md](../deploy/cloud.md) | Cloud VPS / managed services: decision criteria, candidate shortlist (TBD) |
| [../deploy/hybrid.md](../deploy/hybrid.md) | Hybrid (NUC + Hetzner CX21): topology, WireGuard, PG replication, R2, degraded-mode contract, escalation runbook |

## Security

| File | Topic |
|---|---|
| [THREATMODEL.md](THREATMODEL.md) | STRIDE-flavored threat surfaces, mitigations, residual risks |

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
10. **Soft/hard delete flows** — `process_hard_deletes` Celery beat task, grace-period notifications, cascade deletion.

**End of Phase 1:** Sign in → connect Calendar → scan runs → draft appears → edit → publish.
