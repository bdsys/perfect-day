#!/usr/bin/env bash
# bootstrap-local.sh — one-command local dev setup
# Idempotent: safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${REPO_ROOT}/apps/api"
WEB_DIR="${REPO_ROOT}/apps/web"

echo "=== Perfect Day — Local Bootstrap ==="
echo "Repo: ${REPO_ROOT}"

# ---- 1. Create .env from example if missing ----
if [ ! -f "${API_DIR}/.env" ]; then
  echo "→ Creating ${API_DIR}/.env from .env.example"
  cp "${API_DIR}/.env.example" "${API_DIR}/.env"
  echo "→ Generating secrets..."
  SECRETS=$(bash "${REPO_ROOT}/scripts/gen-secrets.sh")
  # Inject generated secrets into .env (replace placeholder values)
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    # Only replace keys that exist in .env
    if grep -q "^${key}=" "${API_DIR}/.env"; then
      sed -i.bak "s|^${key}=.*|${key}=${value}|" "${API_DIR}/.env"
    fi
  done <<< "$SECRETS"
  rm -f "${API_DIR}/.env.bak"
  echo "✓ .env created. Edit ${API_DIR}/.env to add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ANTHROPIC_API_KEY."
else
  echo "✓ .env already exists — skipping creation"
fi

# ---- 2. Start infrastructure ----
echo "→ Starting postgres, redis, minio..."
docker compose -f "${REPO_ROOT}/docker-compose.yml" up -d postgres redis minio

echo "→ Waiting for postgres..."
"${REPO_ROOT}/scripts/wait-for-healthy.sh" "http://localhost:9000/minio/health/live" 60
# Postgres via pg_isready
for i in $(seq 1 20); do
  docker compose -f "${REPO_ROOT}/docker-compose.yml" exec -T postgres \
    pg_isready -U perfectday > /dev/null 2>&1 && break
  sleep 3
done

# ---- 3. Seed MinIO bucket ----
echo "→ Seeding MinIO bucket..."
"${REPO_ROOT}/scripts/seed-minio-bucket.sh"

# ---- 4. Python env + migrations ----
if [ ! -d "${API_DIR}/.venv" ]; then
  echo "→ Creating Python venv..."
  python3 -m venv "${API_DIR}/.venv"
fi
echo "→ Installing Python deps..."
"${API_DIR}/.venv/bin/pip" install -q -e "${API_DIR}[dev]"

echo "→ Running Alembic migrations..."
cd "${API_DIR}" && .venv/bin/alembic upgrade head
cd "${REPO_ROOT}"

# ---- 5. Node / pnpm ----
if command -v pnpm &>/dev/null; then
  echo "→ Installing Node deps (pnpm)..."
  pnpm install --dir "${REPO_ROOT}"
else
  echo "→ pnpm not found, using npm..."
  npm install --prefix "${WEB_DIR}"
fi

echo ""
echo "=== Bootstrap complete ==="
echo "  make up        — start all Docker services"
echo "  make api       — run FastAPI locally (hot-reload)"
echo "  make worker    — run Celery worker"
echo "  make web       — run Next.js dev server"
echo "  make test-fast — unit tests"
echo "  make test      — unit + integration"
echo ""
echo "Edit ${API_DIR}/.env to add your API keys before running the stack."
