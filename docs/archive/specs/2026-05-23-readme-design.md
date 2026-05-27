# README Design — repo root

**Date:** 2026-05-23  
**Repo:** `bdsys/perfect-day`

---

## Goal

Add a `README.md` at the repo root that makes the project look professional and
well-maintained, and helps any visitor orient themselves quickly. Also add Codecov
integration so the coverage badge shows live data.

---

## Approach

Minimal professional: tight structure, no prose fluff, nothing that will rot before
Phase 2. Modelled on well-maintained OSS repos (FastAPI, Vercel projects).

---

## Badge row

Seven badges, rendered on one line:

| # | Badge | Source |
|---|-------|--------|
| 1 | Lint | GitHub Actions job badge (Shields.io workflow badge, label="lint") |
| 2 | Unit Tests | GitHub Actions job badge (label="unit tests") |
| 3 | Integration Tests | GitHub Actions job badge (label="integration tests") |
| 4 | Web Build | GitHub Actions job badge (label="web build") |
| 5 | Docker Build | GitHub Actions job badge (label="docker build") |
| 6 | Security Scan | GitHub Actions job badge (label="security scan") |
| 7 | Coverage | Codecov badge (`bdsys/perfect-day`) |

**Badge URL pattern for GitHub Actions (Shields.io):**
```
https://img.shields.io/github/actions/workflow/status/bdsys/perfect-day/ci.yml
  ?label=lint&style=flat-square
```

Each badge links to `https://github.com/bdsys/perfect-day/actions/workflows/ci.yml`.

**Codecov badge:**
```
https://codecov.io/gh/bdsys/perfect-day/graph/badge.svg?token=<TOKEN>
```
Links to `https://codecov.io/gh/bdsys/perfect-day`.

---

## Codecov integration (CI change required)

Add a final step to the `test-integration` job in `ci.yml` (integration tests produce
the most meaningful coverage since they hit real DB/Redis containers):

```yaml
- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v4
  with:
    token: ${{ secrets.CODECOV_TOKEN }}
    files: apps/api/coverage.xml
    flags: integration
    fail_ci_if_error: false
```

The integration test run must also generate `coverage.xml`:
```yaml
run: pytest tests/unit tests/integration -q --timeout=120 --cov=app --cov-report=xml
```

**Secret required:** `CODECOV_TOKEN` — set via `gh secret set CODECOV_TOKEN`.

---

## README structure

```
# Perfect Day

<badge row>

> One-line description (tagline)

---

## Quick start

Four commands only: bootstrap, api, web, test.
One line pointing to POC_PHASE1_LOCAL_TESTING.md for the full walkthrough.

---

## Where to go next

Table: 4 rows — get stack running, see what's left, deploy to NUC, understand architecture.
Each row: "If you want to..." → link to the right doc.

---

## All documentation

### Design & architecture
Table of all design/ files with descriptions (mirrors design/README.md but more concise).

### Deployment
Table of deploy/ files.

### Operations & reference
Root-level operational docs: PORTS.md, POC_PHASE1_DEPLOYMENT.md, POC_PHASE1_LOCAL_TESTING.md,
POC_PHASE1_TODO.md, scripts/.
```

---

## Two-tier index — "Where to go next" entries

| If you want to… | File |
|-----------------|------|
| Get the stack running locally | `POC_PHASE1_LOCAL_TESTING.md` |
| See what's left to build | `POC_PHASE1_TODO.md` |
| Deploy to the NUC | `POC_PHASE1_DEPLOYMENT.md` |
| Understand the architecture | `design/01-architecture.md` |

---

## All documentation — full tables

### Design & architecture (`design/`)

| File | Topic |
|------|-------|
| `design/README.md` | Index of all design docs |
| `design/01-architecture.md` | Component diagram, service topology |
| `design/02-data-model.md` | Full Postgres schema |
| `design/03-api-surface.md` | FastAPI endpoints |
| `design/04-llm-integration.md` | Prompt structure, anti-hallucination |
| `design/05-google-oauth-integrations.md` | Auth providers, Calendar/Photos grant flow |
| `design/06-scan-worker.md` | Celery beat, scan loop, backfill |
| `design/07-notifications.md` | Push + email channels |
| `design/08-security-privacy.md` | Photo encryption, JWT, deletion flows |
| `design/09-poc-scope.md` | Phase 1/2/3 build order |
| `design/10-open-questions.md` | Resolved architectural decisions (OQ-1–OQ-11) |
| `design/ci-cd.md` | GitHub Actions pipelines, deploy procedure |
| `design/dns-and-email.md` | DNS topology, SPF/DKIM/DMARC |
| `design/observability.md` | Sentry + Grafana Cloud + Better Stack |
| `design/secrets.md` | Secret inventory, rotation, compromise response |
| `design/testing.md` | Test pyramid, coverage targets, CI integration |
| `design/time-and-tz.md` | Timezone rules, DST handling |
| `design/THREATMODEL.md` | STRIDE threat surfaces and mitigations |

### Deployment (`deploy/`)

| File | Topic |
|------|-------|
| `deploy/nuc.md` | Self-hosted Intel NUC: resource budget, edge config, backup |
| `deploy/cloud.md` | Cloud VPS / managed services |
| `deploy/hybrid.md` | NUC + Hetzner hybrid: WireGuard, PG replication |
| `deploy/cloudflare.md` | Cloudflare DNS + DDNS setup |

### Operations & reference

| File | Topic |
|------|-------|
| `PORTS.md` | Port assignments for all services |
| `POC_PHASE1_LOCAL_TESTING.md` | Local dev setup walkthrough |
| `POC_PHASE1_DEPLOYMENT.md` | NUC deployment step-by-step |
| `POC_PHASE1_TODO.md` | Current state and next steps |
| `CLAUDE.md` | AI assistant instructions for this repo |

---

## What is NOT in the README

- Product roadmap or feature list (lives in `design/09-poc-scope.md`)
- Architecture diagrams (lives in `design/01-architecture.md`)
- Detailed deployment instructions (lives in `POC_PHASE1_DEPLOYMENT.md`)
- Contribution guide (not applicable for a personal project at PoC stage)

---

## POC_PHASE1_TODO addition

Add a new task to `POC_PHASE1_TODO.md`:

> **Step 0 (before merge) — Add README + Codecov**
> - Create `README.md` at repo root per spec `docs/superpowers/specs/2026-05-23-readme-design.md`
> - Sign up at codecov.io, connect `bdsys/perfect-day`, copy token
> - `gh secret set CODECOV_TOKEN`
> - Update `ci.yml` integration test job to generate `coverage.xml` and upload to Codecov

---

## Verification steps

1. Push branch → all 6 CI jobs show green badges in README on GitHub
2. Coverage badge shows a percentage (not "unknown") after first successful CI run
3. Clicking each badge navigates to the correct Actions workflow page
4. All doc links in the index resolve (no 404s)
5. `make bootstrap && make api && make web` matches the Quick start block exactly
