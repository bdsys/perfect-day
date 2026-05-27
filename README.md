# Perfect Day

[![Lint](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=lint&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Unit Tests](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=unit%20tests&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Integration Tests](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=integration%20tests&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Web Build](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=web%20build&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Docker Build](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=docker%20build&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Security Scan](https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml?label=security%20scan&style=flat-square)](https://github.com/bdsys/perfect-day/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/bdsys/perfect-day/graph/badge.svg)](https://codecov.io/gh/bdsys/perfect-day)

> An automated diary app that synthesizes Google Calendar, Google Photos, weather, and Spotify into warm, narrative diary entries using Claude — always saved as drafts for human review before publishing.

---

## Quick start

```bash
make bootstrap   # one-time setup: infra, deps, migrations
make api         # FastAPI on :8000
make web         # Next.js on :3000
make test-all    # lint + typecheck + unit + integration + e2e
```

For the full walkthrough see [POC_PHASE1_LOCAL_TESTING.md](POC_PHASE1_LOCAL_TESTING.md).

---

## Where to go next

| If you want to… | File |
|---|---|
| Get the stack running locally | [POC_PHASE1_LOCAL_TESTING.md](POC_PHASE1_LOCAL_TESTING.md) |
| See what's left to build | [POC_PHASE1_TODO.md](POC_PHASE1_TODO.md) |
| Deploy to the NUC | [POC_PHASE1_DEPLOYMENT.md](POC_PHASE1_DEPLOYMENT.md) |
| Understand the architecture | [design/01-architecture.md](design/01-architecture.md) |

---

## All documentation

### Design & architecture (`design/`)

| File | Topic |
|---|---|
| [design/README.md](design/README.md) | Index of all design docs |
| [design/01-architecture.md](design/01-architecture.md) | Component diagram, service topology |
| [design/02-data-model.md](design/02-data-model.md) | Full Postgres schema |
| [design/03-api-surface.md](design/03-api-surface.md) | FastAPI endpoints |
| [design/04-llm-integration.md](design/04-llm-integration.md) | Prompt structure, anti-hallucination |
| [design/05-google-oauth-integrations.md](design/05-google-oauth-integrations.md) | Auth providers, Calendar/Photos grant flow |
| [design/06-scan-worker.md](design/06-scan-worker.md) | Celery beat, scan loop, backfill |
| [design/07-notifications.md](design/07-notifications.md) | Push + email channels |
| [design/08-security-privacy.md](design/08-security-privacy.md) | Photo encryption, JWT, deletion flows |
| [design/09-poc-scope.md](design/09-poc-scope.md) | Phase 1/2/3 build order |
| [design/10-open-questions.md](design/10-open-questions.md) | Resolved architectural decisions (OQ-1–OQ-11) |
| [design/ci-cd.md](design/ci-cd.md) | GitHub Actions pipelines, deploy procedure |
| [design/dns-and-email.md](design/dns-and-email.md) | DNS topology, SPF/DKIM/DMARC |
| [design/observability.md](design/observability.md) | Sentry + Grafana Cloud + Better Stack |
| [design/secrets.md](design/secrets.md) | Secret inventory, rotation, compromise response |
| [design/testing.md](design/testing.md) | Test pyramid, coverage targets, CI integration |
| [design/time-and-tz.md](design/time-and-tz.md) | Timezone rules, DST handling |
| [design/THREATMODEL.md](design/THREATMODEL.md) | STRIDE threat surfaces and mitigations |

### Deployment (`deploy/`)

| File | Topic |
|---|---|
| [deploy/nuc.md](deploy/nuc.md) | Self-hosted Intel NUC: resource budget, edge config, backup |
| [deploy/nuc-ops.md](deploy/nuc-ops.md) | NUC day-to-day operations runbook |
| [deploy/cloud.md](deploy/cloud.md) | Cloud VPS / managed services |
| [deploy/hybrid.md](deploy/hybrid.md) | NUC + Hetzner hybrid: WireGuard, PG replication |
| [deploy/cloudflare.md](deploy/cloudflare.md) | Cloudflare DNS + DDNS setup |

### Operations & reference

| File | Topic |
|---|---|
| [PORTS.md](PORTS.md) | Port assignments for all services |
| [POC_PHASE1_LOCAL_TESTING.md](POC_PHASE1_LOCAL_TESTING.md) | Local dev setup walkthrough |
| [POC_PHASE1_DEPLOYMENT.md](POC_PHASE1_DEPLOYMENT.md) | NUC deployment step-by-step |
| [POC_PHASE1_TODO.md](POC_PHASE1_TODO.md) | Current state and next steps |
| [CLAUDE.md](CLAUDE.md) | AI assistant instructions for this repo |
