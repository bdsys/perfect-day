# POC Phase 1 — Local Testing Guide

This guide walks through setting up, running, and validating the Phase 1 PoC of Perfect Day on your local machine. The goal is to exercise the complete golden path: **sign in → connect Google Calendar → scan → draft entry → edit → publish**.

Covers: macOS and Linux (x86_64 and ARM). Requires Docker.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker + Docker Compose | 24+ | https://docs.docker.com/get-docker/ |
| Python | 3.12+ | `brew install python@3.12` or `apt install python3.12` |
| Node.js | 20+ | `brew install node` or `nvm install 20` |
| pnpm | 9+ | `npm install -g pnpm` |
| jq | any | `brew install jq` or `apt install jq` |
| mc (MinIO client) | any | Optional. Used by `seed-minio-bucket.sh`. Falls back to Docker if absent. |

Verify:
```bash
docker compose version    # must show v2+
python3 --version         # 3.12.x
node --version            # v20.x
```

---

## Quick Start (One Command)

```bash
# From the repo root:
make bootstrap
```

This runs `scripts/bootstrap-local.sh` which:
1. Creates `apps/api/.env` from `.env.example` and generates cryptographic secrets.
2. Starts `postgres`, `redis`, and `minio` via Docker Compose.
3. Waits for all three to be healthy.
4. Creates the `photos` MinIO bucket.
5. Installs Python deps (`pip install -e ".[dev]"`).
6. Runs Alembic migrations (`alembic upgrade head`).
7. Installs Node deps.

Bootstrap is **idempotent** — safe to re-run.

After bootstrap, open `apps/api/.env` and add your API keys:
```
GOOGLE_CLIENT_ID=...        # Required for Calendar OAuth
GOOGLE_CLIENT_SECRET=...    # Required for Calendar OAuth
ANTHROPIC_API_KEY=...       # Required for LLM draft generation
```
These can be left blank to test auth, diary CRUD, and entry management without the integrations.

See **[Obtaining API Keys](#obtaining-api-keys)** below for step-by-step instructions on creating these credentials.

---

## Running the Stack

### Option A — Full Docker stack

```bash
make up          # docker compose up -d (all 7 services)
make logs        # follow all logs
```

Services started: `postgres`, `redis`, `minio`, `pgadmin`, `api`, `worker`, `beat`, `web`.

### Option B — Hot-reload local dev (recommended for iteration)

Keep infra in Docker, run app services locally for faster feedback:

```bash
make up          # starts postgres, redis, minio only (compose handles deps)
# Then in separate terminal panes:
make api         # FastAPI on :8000 with --reload
make worker      # Celery worker
make beat        # Celery beat scheduler
make web         # Next.js dev server on :3000
```

### Service endpoints

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | FastAPI |
| API docs | http://localhost:8000/docs | Swagger UI (dev mode only) |
| Web UI | http://localhost:3000 | Next.js |
| MinIO console | http://localhost:9001 | minioadmin / minioadmin |
| pgAdmin 4 | http://localhost:5050 | Postgres web GUI; admin@example.com / admin |

---

## Running the Test Suite

### Run everything at once (`make test-all`)

```bash
make test-all
```

Chains lint → typecheck → unit+integration tests → end-to-end (Playwright), fail-fast, in about 10 minutes total. This is the recommended command to run after any non-trivial change. It does **not** include `make test-live` (calls the real Anthropic API — see below) or `./scripts/smoke-test.sh` (requires a running stack).

### Unit tests only (~30s)

```bash
make test-fast
```

Tests: `apps/api/tests/unit/` — argon2/JWT/AES-GCM, timezone utilities, citation validator, config validation.

### Unit + integration tests (~3–5 min first run)

```bash
make test
```

Integration tests spin up **real** Postgres, Redis, and MinIO containers via `testcontainers` — no mocking of these services (see `design/testing.md` § Mocking policy). Requires Docker. Subsequent runs use cached container images (~1 min).

### With coverage report

```bash
make test-coverage
# HTML report at apps/api/htmlcov/index.html
```

### TypeScript + mypy

```bash
make typecheck
```

### Lint

```bash
make lint
```

### End-to-end smoke (Playwright)

```bash
make test-e2e
```

This:
1. Boots the full stack via `docker-compose.test.yml` overlay (ephemeral volumes, deterministic `PYTHONHASHSEED`).
2. Waits for `/readyz`.
3. Runs `apps/web/e2e/golden-path.spec.ts` — the 5-step Phase 1 smoke.
4. Tears down and removes volumes.

Playwright report on failure: `apps/web/playwright-report/`.

### Live LLM goldens (manual, never in CI)

```bash
make test-live
# Requires ANTHROPIC_API_KEY set in apps/api/.env
```

Calls the real Anthropic API. Use this to refresh `tests/cassettes/llm_draft_simple.yaml`.

---

## Automated Smoke (curl)

`scripts/smoke-test.sh` runs a full curl walkthrough and asserts HTTP status codes at each step. It exercises every Phase 1 API endpoint and exits non-zero on any failure.

```bash
./scripts/smoke-test.sh http://localhost:8000
```

You can also run it against a deployed stack:
```bash
./scripts/smoke-test.sh https://api.diary.perfectday.andrewlass.com
```

---

## Phase 1 Validation Matrix

Each row maps a Phase 1 scope item (`design/09-poc-scope.md` items 1–10) to the command that validates it.

| # | Scope item | Validated by |
|---|---|---|
| 1 | Postgres schema + Alembic migrations | `make migrate` exits 0; `psql -c "\dt"` lists ~20 tables |
| 2 | FastAPI skeleton (health, CORS, rate limit) | `curl /healthz`, `curl /readyz`; both return 200 |
| 3 | Auth: email+password + Google OAuth login | `make test` → `test_auth_flow.py`; `smoke-test.sh` register/login steps |
| 4 | Diary + Entry CRUD | `make test` → `test_diaries.py`, `test_entries.py`; `smoke-test.sh` diary/entry steps |
| 5 | Google Calendar OAuth grant | `make test` → `test_google_oauth.py`; manual browser flow with real GCP creds |
| 6 | Celery + Redis (worker + beat) | `make worker` starts; `smoke-test.sh` `/scan/run` step queues task; check worker logs |
| 7 | Scan worker: calendar only | Integration test `test_scan_loop.py`; manual trigger via `curl /v1/diaries/{id}/scan/run` |
| 8 | LLM draft generation | `make test-live`; or manual entry regenerate from UI |
| 9 | Web UI: timeline + entry detail | `make test-e2e` runs golden-path.spec.ts (steps 1–5) |
| 10 | Soft/hard delete flows | `make test` → `test_hard_delete.py`; account delete via `/v1/auth/account` |

---

## Troubleshooting

**`GET /readyz` returns 503**
MinIO `photos` bucket doesn't exist. Run:
```bash
./scripts/seed-minio-bucket.sh
```

**Alembic migration fails with `citext extension does not exist`**
Your Postgres container is missing the extension. The migration enables it automatically via `CREATE EXTENSION IF NOT EXISTS citext`. If you're using an external Postgres, ensure the `citext` extension is available. The official `postgres:16-alpine` image includes it.

**Celery worker shows `KeyError: 'redis'` or `ConnectionRefusedError`**
Redis is not healthy. Check: `docker compose ps redis`. If it shows `unhealthy`, `docker compose restart redis`.

**`CORS error` in browser console when calling the API**
`CORS_ORIGINS` in `apps/api/.env` doesn't include `http://localhost:3000`. Fix:
```
CORS_ORIGINS=["http://localhost:3000"]
```
Then restart the API.

**`DATABASE_URL` / `DATABASE_URL_SYNC` confusion**
`DATABASE_URL` must use `postgresql+asyncpg://` (for SQLAlchemy async). `DATABASE_URL_SYNC` must use plain `postgresql://` (for Alembic). Both are in `.env.example`.

**Unit tests fail with `pydantic ValidationError: master_secret`**
`MASTER_SECRET` env var is not set or is not 64 hex characters. The test `conftest.py` sets it automatically for unit tests. If running pytest manually outside the Makefile, ensure env vars are set:
```bash
export MASTER_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

**`docker compose up web` fails with `dockerfile not found`**
`apps/web/Dockerfile` is required. It was created as part of this repo's Phase 1 setup and should exist at `apps/web/Dockerfile`. If it's missing, check git status.

---

## Obtaining API Keys

### Google Cloud — Project Strategy

The plan uses **three separate GCP projects** to keep credentials isolated and avoid accidentally hitting production quotas during development:

| GCP Project | Purpose | OAuth redirect URIs |
|---|---|---|
| `perfect-day-dev` | Local development | `http://localhost:8000/v1/integrations/google/callback` |
| `perfect-day-test` | CI / automated testing | (same as dev) |
| `perfect-day-prod` | NUC production | `https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback` |

For local testing, create and use `perfect-day-dev`.

---

### Step 1 — Create a GCP project

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Click the project dropdown at the top → **New Project**.
3. Name: `perfect-day-dev` → **Create**.
4. Make sure the new project is selected in the dropdown before continuing.

---

### Step 2 — Enable APIs

1. In the left sidebar: **APIs & Services → Library**.
2. Search for and enable each of the following:
   - **Google Calendar API**
   - *(Phase 2, skip for now)* Google Photos Library API

---

### Step 3 — Configure OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** → **Create**.
3. Fill in required fields:
   - App name: `Perfect Day (dev)`
   - User support email: your Google account email
   - Developer contact email: same
4. Click **Save and Continue** through Scopes (add none here — they're requested at runtime).
5. On the **Test users** step, add your own Google account email address.  
   *(This keeps the app in "testing" mode — only listed test users can authorize. You won't need to go through Google's verification process for local dev.)*
6. **Save and Continue** → **Back to Dashboard**.

---

### Step 4 — Create OAuth 2.0 credentials

1. **APIs & Services → Credentials → + Create Credentials → OAuth client ID**.
2. Application type: **Web application**.
3. Name: `Perfect Day dev web`.
4. Under **Authorized redirect URIs**, click **Add URI** and enter:
   ```
   http://localhost:8000/v1/integrations/google/callback
   ```
5. Click **Create**.
6. A dialog shows your **Client ID** and **Client secret** — copy both now (you can retrieve them again later from the Credentials page).

---

### Step 5 — Add Google credentials to `.env`

Open `apps/api/.env` and set:
```
GOOGLE_CLIENT_ID=<your Client ID from step 4>
GOOGLE_CLIENT_SECRET=<your Client secret from step 4>
```

---

### Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com).
2. Sign in (or create an account).
3. In the left sidebar: **API Keys → + Create Key**.
4. Name: `perfect-day-dev` → **Create Key**.
5. Copy the key immediately — it is only shown once.
6. Open `apps/api/.env` and set:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

> **Note:** LLM draft generation is only triggered when a scan completes and finds calendar events. If you just want to test auth and diary CRUD, you can leave `ANTHROPIC_API_KEY` blank.

---

### Verifying credentials work

After setting the keys and restarting the API (`make api`):

**Google OAuth:**
```bash
# Register and get a token
TOKEN=$(curl -s -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"Password1!"}' | jq -r .access_token)

# Get the Google authorize URL
curl -s http://localhost:8000/v1/integrations/google/authorize?scopes=calendar \
  -H "Authorization: Bearer $TOKEN" | jq .url
# Should return a URL starting with https://accounts.google.com/...
# Open that URL in a browser and complete the flow with your test user account
```

**Anthropic:**
```bash
# LLM is invoked automatically during scan — check worker logs after triggering a scan:
curl -s -X POST http://localhost:8000/v1/diaries/<diary_id>/scan/run \
  -H "Authorization: Bearer $TOKEN"
# Then: make logs | grep llm
```

---

## Outstanding Phase 1 TODOs

These items are in scope for Phase 1 but not yet implemented. Pick them up before
considering Phase 1 complete.

### 1 — Restore UI for soft-deleted diaries and entries

**Status:** Backend endpoints exist and work; frontend UI is missing.

The API already has:
- `POST /v1/diaries/{id}/restore` — restores a soft-deleted diary within the 30-day window
- `POST /v1/entries/{id}/restore` — restores a soft-deleted entry (no grace-period constraint currently)

What's needed in the web UI:
- `/diaries` page: list soft-deleted diaries (separate section or filter) with a Restore button each.
- `/diaries/{id}` page: list soft-deleted entries (toggle or separate tab) with a Restore button each.
- Wire `api.diaries.restore(id)` and `api.entries.restore(id)` into the API client (`apps/web/src/lib/api.ts`).
- Update the delete confirm dialogs to say something accurate (e.g. "You can restore it from this page within 30 days").

### 2 — Entry hard-delete (30-day grace) + matching UI

**Status:** Entries are soft-deleted indefinitely; no hard-delete path exists for them. The design doc
(`design/02-data-model.md` § Behavior decisions) says entries are "soft indefinitely; recoverable from UI"
but the confirm dialog currently implies a 30-day window which is inaccurate.

Two sub-tasks:

**a) Fix the confirm dialog (quick):**
Change the entry delete confirm in `apps/web/src/app/entries/[entryId]/page.tsx` to not imply a deadline,
since entries are intentionally soft-deleted indefinitely per the design doc.

**b) Decide and document the intended behaviour:**
The design doc says entries are soft-indefinitely. If that's the final decision, update the confirm
dialog to reflect it and make sure the restore UI (TODO #1 above) surfaces soft-deleted entries
with no expiry warning. If a 30-day grace window is preferred instead, add `hard_delete_after` to the
`Entry` model, wire it into `process_hard_deletes` in `apps/api/app/workers/beat_tasks.py`, add an
Alembic migration, and update the restore endpoint to enforce the deadline (matching the diary flow
in `apps/api/app/routers/v1/diaries.py:246`).

---

## Security notes for local dev

- Generated secrets in `apps/api/.env` are for local use only. Never commit `.env` (it's in `.gitignore`).
- `MASTER_SECRET` and `OAUTH_TOKEN_SECRET` are AES-256-GCM encryption keys. If you rotate them after the database has data, existing encrypted rows become unreadable. See `POC_PHASE1_DEPLOYMENT.md` for the upgrade path to sops+YubiKey.
- MinIO admin credentials (`minioadmin`/`minioadmin`) are only used locally. The NUC deployment uses random credentials via `scripts/nuc/10-secrets.sh`.
