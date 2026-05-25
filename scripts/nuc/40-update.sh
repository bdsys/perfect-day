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

echo '[1/7] Updating repository...'
git fetch origin
if [ -n "${TARGET_SHA}" ]; then
    git checkout "${TARGET_SHA}"
    echo "  Checked out sha: ${TARGET_SHA}"
else
    git pull --ff-only
    echo "  Updated to: $(git rev-parse --short HEAD)"
fi

echo '[2/7] Rendering Caddyfile from template...'
ENV_FILE="/etc/perfect-day/app.env"
if [ -f "${ENV_FILE}" ]; then
    FORTIGATE_LAN_IP=$(grep -E '^FORTIGATE_LAN_IP=' "${ENV_FILE}" | cut -d= -f2-)
    export FORTIGATE_LAN_IP
else
    echo "  WARNING: ${ENV_FILE} not found — Caddyfile will use private_ranges fallback." >&2
fi
./scripts/nuc/render-caddyfile.sh

echo '[3/7] Pulling new images...'
docker compose --profile nuc pull api worker beat web edge

echo '[4/7] Running migrations...'
docker compose --profile nuc run --rm api alembic upgrade head
echo '  Migrations complete'

echo '[5/7] Restarting app services (infra stays up)...'
docker compose --profile nuc up -d --no-deps api worker beat web edge
echo '  Services restarted'

echo '[6/7] Waiting for readiness...'
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

echo '[7/7] Update complete.'
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
