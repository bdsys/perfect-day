#!/usr/bin/env bash
# scripts/nuc/40-update.sh — Pull new images, migrate, restart app services
# Usage: sudo ./scripts/nuc/40-update.sh [sha]
# Example: sudo ./scripts/nuc/40-update.sh
#          sudo ./scripts/nuc/40-update.sh abc1234
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

TARGET_SHA="${1:-}"
DEPLOY_DIR="/opt/perfect-day"
HEALTH_URL="https://api.diary.perfectday.andrewlass.com/readyz"
HEALTH_TIMEOUT=90
SMOKE_SCRIPT="./scripts/smoke-test.sh"

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/update-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Perfect Day Update ==="
[ -n "${TARGET_SHA}" ] && echo "SHA:    ${TARGET_SHA}" || echo "SHA:    HEAD (latest)"
echo ""

cd "${DEPLOY_DIR}"

echo '[1/6] Updating repository...'
git fetch origin
if [ -n "${TARGET_SHA}" ]; then
    git checkout "${TARGET_SHA}"
    echo "  Checked out sha: ${TARGET_SHA}"
else
    git pull --ff-only
    echo "  Updated to: $(git rev-parse --short HEAD)"
fi

echo '[2/6] Pulling new images...'
docker compose pull api worker beat web

echo '[3/6] Running migrations...'
docker compose run --rm api alembic upgrade head
echo '  Migrations complete'

echo '[4/6] Restarting app services (infra stays up)...'
docker compose up -d --no-deps api worker beat web
echo '  Services restarted'

echo '[5/6] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 "${HEALTH_URL}" > /dev/null 2>&1; do
    if [ "${ELAPSED}" -ge "${HEALTH_TIMEOUT}" ]; then
        echo "ERROR: Service not healthy after ${HEALTH_TIMEOUT}s — rolling back." >&2
        echo 'Run: sudo ./scripts/nuc/50-rollback.sh' >&2
        exit 1
    fi
    sleep 5
    ELAPSED=$(( ELAPSED + 5 ))
    echo "  Waiting... ${ELAPSED}s"
done

SHA=$(git rev-parse --short HEAD)
echo "${SHA}" > "${DEPLOY_DIR}/last-deployed-sha"
echo "  Recorded deployed SHA: ${SHA}"

echo '[6/6] Update complete.'
echo "Log: ${LOG_FILE}"

echo ""
echo "Running smoke test against ${HEALTH_URL%/readyz}..."
if "${SMOKE_SCRIPT}" "${HEALTH_URL%/readyz}" 2>/dev/null; then
    echo "Smoke test passed."
else
    echo "WARNING: Smoke test failed. Check logs or run rollback:"
    echo "  sudo ./scripts/nuc/50-rollback.sh"
    exit 1
fi
