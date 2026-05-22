.PHONY: up down api worker beat web migrate lint typecheck \
        test test-fast test-e2e test-live test-coverage \
        seed-bucket bootstrap

API_DIR := apps/api
WEB_DIR := apps/web

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# Run services individually (useful when iterating on one layer)
api:
	cd $(API_DIR) && uvicorn app.main:app --reload --port 8000

worker:
	cd $(API_DIR) && celery -A app.workers.celery_app worker --loglevel=info --concurrency=2

beat:
	cd $(API_DIR) && celery -A app.workers.celery_app beat --loglevel=info

web:
	cd $(WEB_DIR) && npm run dev

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

migrate:
	cd $(API_DIR) && alembic upgrade head

migrate-down:
	cd $(API_DIR) && alembic downgrade -1

seed-bucket:
	./scripts/seed-minio-bucket.sh

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint:
	cd $(API_DIR) && ruff check app tests
	cd $(WEB_DIR) && npx eslint src --max-warnings 0

typecheck:
	cd $(API_DIR) && mypy app
	cd $(WEB_DIR) && npx tsc --noEmit

format:
	cd $(API_DIR) && ruff format app tests

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test-fast:
	cd $(API_DIR) && pytest tests/unit -q

test:
	cd $(API_DIR) && pytest tests/unit tests/integration -q

test-coverage:
	cd $(API_DIR) && pytest tests/unit tests/integration \
	  --cov=app --cov-report=term-missing --cov-report=html:htmlcov -q

test-e2e:
	docker compose -f docker-compose.yml -f docker-compose.test.yml up -d
	./scripts/wait-for-healthy.sh http://localhost:8000/readyz 60
	cd $(WEB_DIR) && npx playwright test
	docker compose -f docker-compose.yml -f docker-compose.test.yml down -v

test-live:
	@echo "Runs live LLM golden tests — never in CI. Requires ANTHROPIC_API_KEY."
	cd $(API_DIR) && pytest tests/integration/test_scan_loop.py -q -m live

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

bootstrap:
	./scripts/bootstrap-local.sh
