# POC Phase 1 — Master Todo

Where things stand as of 2026-05-25 and what is left to do.

> **Operational runbook moved.** All NUC deployment/update/rebuild/rollback procedures live in [`deploy/nuc-ops.md`](deploy/nuc-ops.md). This file now tracks only Phase 1 status, the dependency map, and known gaps.

---

## Current State

- PR #3 (`poc-p1`) has been **merged to `main`**. ✅
- Backend API is **complete** for Phase 1: auth, diary/entry CRUD, Google OAuth, scan worker, hard-delete flows, rate limiting.
- Local environment is **set up and validated** — unit + integration tests pass, smoke test clean. ✅
- Web UI has real pages (login, register, diary list, diary timeline, entry detail) but **not yet tested end-to-end** against the live API.
- Caddyfile templating fix landed (see commit `963bb40`) — `FORTIGATE_LAN_IP` is now rendered into `deploy/caddy/Caddyfile` from `Caddyfile.tmpl` at every deploy. ✅

**Phase 1 is complete.** Infrastructure (NUC, Cloudflare, CD) is Phase 3. Application features are Phase 2.

---

## What is left

### Phase A — Web UI audit (done ✅)

All routes tested against the live local stack. Restore UI (soft-delete + grace-period countdown) complete.

### Phase B — README (done ✅)

`README.md` created at repo root per `docs/superpowers/specs/2026-05-23-readme-design.md`.

> **Note (Phase 2):** Wire up `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in `apps/web/.env.local` when Google OAuth / Photos integration is added in Phase 2. Email+password login is sufficient for Phase 1.

---

## Phase 3 (deferred — infrastructure)

NUC deployment, Cloudflare/FortiGate configuration, and CD wiring are deferred until the application features are stable. See `deploy/nuc-ops.md` for the full step-by-step procedures when ready.

| Phase | What | Notes |
|---|---|---|
| 3A | Third-party account setup | Cloudflare DNS hand-off, Google Cloud OAuth project, SendGrid relay, Anthropic API key |
| 3B | NUC deployment | Bootstrap → secrets → first deploy → DDNS → FortiGate TLS certs → Google prod redirect URI → backups → smoke test |
| 3C | CD wiring | Deploy SSH key, `GHCR_TOKEN`, `DEPLOY_ENABLED` → push-to-deploy automated |

---

## Dependency map

```
Phase 1: DONE ✅

Phase 2 (application features — see design/09-poc-scope.md):
  Google Photos → MinIO → photo scan
  Weather enrichment (Open-Meteo)
  Backfill, tier enforcement, notifications, admin panel, …
  Wire up Google OAuth (NEXT_PUBLIC_GOOGLE_CLIENT_ID) for Google Sign-In UI

Phase 3 (deferred infrastructure):
  3A (Cloudflare + Google Cloud prod + SendGrid + Anthropic keys)
    └─ 3B (NUC deployment)
         └─ 3C (CD wiring)
```

Step-by-step NUC/Cloudflare procedures are in [`deploy/nuc-ops.md`](deploy/nuc-ops.md).

---

## Known issues and technical debt

See [`design/known-issues.md`](design/known-issues.md).
