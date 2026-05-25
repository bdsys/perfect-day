.PHONY: up down infra api worker beat web migrate lint typecheck \
        test test-fast test-all test-e2e test-live test-coverage \
        web-e2e-install seed-bucket bootstrap

API_DIR  := apps/api
WEB_DIR  := apps/web
PYTEST   := $(API_DIR)/.venv/bin/pytest
VENV_BIN := $(API_DIR)/.venv/bin

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

# Option A: run everything in Docker (no hot-reload)
up:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d

down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev down

logs:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev logs -f

# Option B: infra in Docker + app processes locally (hot-reload dev)
#   Step 1: make infra          — start postgres, redis, minio only
#   Step 2 (separate terminals):
#     make api    — FastAPI with --reload on :8000
#     make worker — Celery worker
#     make beat   — Celery beat scheduler
#     make web    — Next.js dev server on :3000
infra:
	docker compose up -d postgres redis minio

# Run app processes locally (use after `make infra`, not after `make up`)
api:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/uvicorn app.main:app --reload --port 8000

worker:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/celery -A app.workers.celery_app worker --loglevel=info --concurrency=2

beat:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/celery -A app.workers.celery_app beat --loglevel=info

web:
	cd $(WEB_DIR) && npm run dev

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

migrate:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/alembic upgrade head

migrate-down:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/alembic downgrade -1

seed-bucket:
	./scripts/seed-minio-bucket.sh

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/ruff check app tests
	cd $(WEB_DIR) && npx eslint src --max-warnings 0

typecheck:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/mypy app
	cd $(WEB_DIR) && npx tsc --noEmit

format:
	cd $(API_DIR) && $(CURDIR)/$(VENV_BIN)/ruff format app tests

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test-fast:
	cd $(API_DIR) && $(CURDIR)/$(PYTEST) tests/unit -q

test:
	cd $(API_DIR) && $(CURDIR)/$(PYTEST) tests/unit tests/integration -q

# Run lint → typecheck → unit+integration → e2e in fail-fast order (~10 min).
# Excludes test-live (real API cost) and smoke-test.sh (needs a running stack).
test-all:
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test
	@$(MAKE) test-e2e
	@echo ""
	@echo "All checks passed."
	@echo "Note: 'make test-live' and './scripts/smoke-test.sh' are not included here."
	@echo "See POC_PHASE1_LOCAL_TESTING.md for when to use them."

test-coverage:
	cd $(API_DIR) && $(CURDIR)/$(PYTEST) tests/unit tests/integration \
	  --cov=app --cov-report=term-missing --cov-report=html:htmlcov -q

test-e2e:
	docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build web
	./scripts/wait-for-healthy.sh http://localhost:8000/readyz 60
	cd $(API_DIR) && DATABASE_URL_SYNC=postgresql://perfectday:perfectday@localhost:5432/perfectday_test \
	  $(CURDIR)/$(VENV_BIN)/alembic upgrade head
	test -d "$$HOME/Library/Caches/ms-playwright" || $(MAKE) web-e2e-install
	cd $(WEB_DIR) && CI=1 npx playwright test
	docker compose -f docker-compose.yml -f docker-compose.test.yml down -v

web-e2e-install:
	cd $(WEB_DIR) && npx playwright install chromium

test-live:
	@echo "Runs live LLM golden tests — never in CI. Requires ANTHROPIC_API_KEY."
	cd $(API_DIR) && ANTHROPIC_API_KEY=$$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-) ANTHROPIC_BASE_URL=https://api.anthropic.com $(CURDIR)/$(PYTEST) tests/integration/test_llm_live.py -q -m live

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

bootstrap:
	./scripts/bootstrap-local.sh
