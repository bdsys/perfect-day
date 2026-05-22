# CI/CD

Closes M24. Platform: GitHub Actions. Branch model: trunk-based (`main` is always deployable).

## Branch model

- `main` — production-deployable at all times
- Feature branches — short-lived; merged to `main` via PR
- No long-lived release branches for PoC
- No force-push to `main`; branch protection enforces it

## Workflows

### `.github/workflows/ci.yml` — runs on every push to any branch

```
Steps:
  1. Checkout
  2. Set up Python (uv) + Node (pnpm)
  3. Restore pip/pnpm/Docker layer caches
  4. Lint: ruff (Python), eslint + tsc (TypeScript)
  5. Unit tests: pytest apps/api/tests/unit/
  6. Integration tests: pytest apps/api/tests/integration/ (testcontainers)
  7. Security scan: pip-audit, pnpm audit (fail on high/critical)
  8. Build Docker images (api, web, worker) — validate build only; no push
  9. Trivy image scan — fail on critical CVEs
```

Runtime budget: ~8–10 min total.

Caches:
- `pip` via `uv` lock file hash
- `pnpm` via `pnpm-lock.yaml` hash
- Docker layer cache via GitHub Actions cache (`type=gha`)

### `.github/workflows/e2e.yml` — runs on PR open + `run-e2e` label

```
Steps:
  1. Checkout
  2. Start full compose stack (docker compose -f docker-compose.yml -f docker-compose.test.yml up -d)
  3. Wait for /readyz
  4. Run Playwright tests
  5. Upload test artifacts (screenshots, videos) on failure
  6. Tear down compose stack
```

Skipped on draft PRs. Requires `run-e2e` label for PRs that don't touch backend or web code (e.g. docs-only PRs).

Runtime budget: ~15–20 min.

Google OAuth in E2E: uses a dedicated test Google Cloud project. Client credentials are stored in GitHub Actions secrets (`E2E_GOOGLE_CLIENT_ID`, `E2E_GOOGLE_CLIENT_SECRET`, `E2E_GOOGLE_TEST_ACCOUNT_REFRESH_TOKEN`).

Anthropic: mocked via HTTP cassettes. No live API calls in CI.

### `.github/workflows/deploy.yml` — runs on merge to `main` after `ci.yml` passes

```
Steps:
  1. Checkout
  2. Build versioned Docker images:
       {service}:sha-{shortsha}
       {service}:latest
  3. Push to GHCR (ghcr.io/{owner}/perfect-day-{service})
  4. Deploy to target:
       — NUC: SSH into host, docker compose pull + up -d (see below)
       — Cloud: see deploy/cloud.md when target is chosen
  5. Post Sentry release marker (sentry-cli releases new {git_sha})
```

Also available as `workflow_dispatch` with `target_sha` input for rollback.

## Image registry

**GHCR** (`ghcr.io/{owner}/perfect-day-{service}`). Free for private repos within GitHub Actions usage quota.

Images:
- `perfect-day-api` — FastAPI application
- `perfect-day-worker` — Celery worker (same codebase, different entry point)
- `perfect-day-beat` — Celery beat scheduler
- `perfect-day-web` — Next.js (built image for production; dev uses Vite dev server)

## Deployment — single-host (NUC)

The deploy step in `deploy.yml` SSHs into the NUC via a dedicated deploy key and runs:

```bash
# Pull new images
docker compose pull api worker beat web

# Run Alembic migrations (one-shot container, must complete before API restart)
docker compose run --rm api alembic upgrade head
# Exit code non-zero → abort deploy

# Restart services (rolling, no downtime for stateless services)
docker compose up -d --no-deps api worker beat web

# Health check — wait up to 60s for /readyz to return 200
./scripts/wait-for-healthy.sh https://diary.perfectday.bdsys.net/readyz 60
# Health check failure → rollback (see below)
```

Migrations run before the API restarts. If Alembic fails, the old API container keeps running (it was not restarted yet). This is a forward-only migration strategy — down-migrations exist in the Alembic chain but are never run automatically.

**Rollback:** `workflow_dispatch` with `target_sha` input pulls the specified SHA's images and runs the same compose-up dance. Database migrations are not auto-rolled back; the operator decides whether to run a down-migration manually.

Full NUC-specific deploy instructions (ports, volumes, restart policies) are in `deploy/nuc.md`.

## Deployment — cloud (deferred)

Cloud target TBD per `deploy/cloud.md`. When chosen, the deploy step in `deploy.yml` adds a branch:

```yaml
- if: env.DEPLOY_TARGET == 'fly'
  run: flyctl deploy --image ghcr.io/${{ github.repository_owner }}/perfect-day-api:sha-${{ env.SHORT_SHA }}
```

## Secrets in CI

GitHub Actions secrets used by the workflows:

| Secret name | Used by | What it is |
|---|---|---|
| `GHCR_TOKEN` | `deploy.yml` | GitHub PAT with `write:packages` for GHCR push |
| `NUC_SSH_PRIVATE_KEY` | `deploy.yml` | Private key for the NUC deploy user |
| `NUC_HOST` | `deploy.yml` | NUC public IP or hostname |
| `SOPS_AGE_KEY` | `deploy.yml` | Age private key to decrypt `secrets/production.enc.yaml` on the host at deploy time |
| `SENTRY_AUTH_TOKEN` | `deploy.yml` | Sentry release + source map upload |
| `E2E_GOOGLE_CLIENT_ID` | `e2e.yml` | Test Google Cloud project client ID |
| `E2E_GOOGLE_CLIENT_SECRET` | `e2e.yml` | Test Google Cloud project client secret |
| `E2E_GOOGLE_TEST_ACCOUNT_REFRESH_TOKEN` | `e2e.yml` | Pre-authorized refresh token for the test Google account |

**Production application secrets** (Anthropic API key, `master_secret`, etc.) never enter GitHub Actions memory. They are decrypted from the sops YAML on the NUC host by the deploy step.

## Release markers

Every successful deploy:
1. Posts a Sentry release: `sentry-cli releases new {git_sha} && sentry-cli releases set-commits {git_sha} --auto && sentry-cli releases finalize {git_sha}`
2. Associates the release with the `production` environment in Sentry.

Future: Grafana deploy annotations can be added via the Grafana Cloud API when dashboards are set up.

## Rollback procedure

```
1. Find the previous stable SHA in git log or Sentry releases.
2. Go to GitHub Actions → deploy.yml → Run workflow → enter target_sha.
3. Workflow pulls images at that SHA, restarts services.
4. If the rollback crosses a database migration, run the Alembic downgrade manually:
     docker compose run --rm api alembic downgrade -1
```

## Branch protection

`main` requires:
- `ci.yml` passing (lint, unit, integration, security scan)
- 1 PR approval (can be waived to 0 during solo PoC — update the branch protection rule)
- No force-push
- No branch deletion

## Dependency updates

**Dependabot** is configured for Python (`apps/api`) and JavaScript (`apps/web`, `packages/api-types`).

`.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /apps/api
    schedule: { interval: weekly }
    groups:
      all-deps: { patterns: ["*"] }    # grouped PR for minor+patch
  - package-ecosystem: npm
    directory: /apps/web
    schedule: { interval: weekly }
    groups:
      all-deps: { patterns: ["*"] }
```

- Minor and patch updates: auto-merge when `ci.yml` passes.
- Major updates: require manual review.
- Security updates (Dependabot alerts): auto-merge when `ci.yml` passes, regardless of version bump size.

## Security scanning

| Tool | What it scans | Fail condition |
|---|---|---|
| `pip-audit` | Python dependency CVEs | Any high or critical |
| `pnpm audit` | JS/TS dependency CVEs | Any high or critical |
| Trivy | Docker image layers | Any critical CVE |

Trivy runs after the image build step. Findings below critical are reported but do not fail CI.
