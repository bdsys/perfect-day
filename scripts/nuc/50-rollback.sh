#!/usr/bin/env bash
# scripts/nuc/50-rollback.sh — Go back one deploy
# Usage: sudo ./scripts/nuc/50-rollback.sh
#
# 40-update.sh tags the running app images `previous` before it builds a new commit,
# and records that commit in previous-deployed-sha. This script puts those images
# back, checks out that commit (so compose and the Caddyfile match the images) and
# restarts. It goes back exactly one deploy; the next 40-update.sh returns to main.
# Database migrations are NOT rolled back (see the note at the end).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

DEPLOY_DIR="/opt/perfect-day"
HEALTH_URL="https://perfectday-api.andrewlass.com/readyz"
HEALTH_TIMEOUT=90
IMAGE_PREFIX="perfect-day"   # compose project name (the deploy directory's name)
APP_SERVICES="api worker beat web"

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/rollback-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Perfect Day Rollback ==="
cd "${DEPLOY_DIR}"

PREV_SHA=$(cat "${DEPLOY_DIR}/previous-deployed-sha" 2>/dev/null || echo '')
if [ -z "${PREV_SHA}" ]; then
    echo "ERROR: ${DEPLOY_DIR}/previous-deployed-sha not found: no earlier deploy to go back to." >&2
    exit 1
fi
for svc in ${APP_SERVICES}; do
    if ! docker image inspect "${IMAGE_PREFIX}-${svc}:previous" > /dev/null 2>&1; then
        echo "ERROR: image ${IMAGE_PREFIX}-${svc}:previous not found. Nothing changed." >&2
        exit 1
    fi
done
echo "Rolling back to ${PREV_SHA} (currently $(cat "${DEPLOY_DIR}/last-deployed-sha" 2>/dev/null || echo unknown))"

echo '[1/4] Checking out the previous commit...'
git checkout -q "${PREV_SHA}"
FORTIGATE_LAN_IP=$(grep -E '^FORTIGATE_LAN_IP=' /etc/perfect-day/app.env 2>/dev/null | cut -d= -f2- || true)
export FORTIGATE_LAN_IP
./scripts/nuc/render-caddyfile.sh

echo '[2/4] Restoring the previous images...'
for svc in ${APP_SERVICES}; do
    docker tag "${IMAGE_PREFIX}-${svc}:previous" "${IMAGE_PREFIX}-${svc}:latest"
done
# shellcheck disable=SC2086
docker compose --profile nuc up -d --no-deps --force-recreate ${APP_SERVICES}
docker compose --profile nuc up -d --no-deps --force-recreate edge
echo '  Services restarted'

echo '[3/4] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 "${HEALTH_URL}" > /dev/null 2>&1; do
    if [ "${ELAPSED}" -ge "${HEALTH_TIMEOUT}" ]; then
        echo "ERROR: Rollback target also unhealthy after ${HEALTH_TIMEOUT}s." >&2
        echo 'Check service logs: docker compose --profile nuc logs --tail=100' >&2
        exit 1
    fi
    sleep 5
    ELAPSED=$(( ELAPSED + 5 ))
    echo "  Waiting... ${ELAPSED}s"
done
echo "${PREV_SHA}" > "${DEPLOY_DIR}/last-deployed-sha"

echo "[4/4] Rollback complete: ${PREV_SHA}"
echo "Log: ${LOG_FILE}"
echo ''
echo 'NOTE: Database migrations are NOT automatically rolled back.'
echo 'If the newer release ran a forward migration that the rolled-back code cannot'
echo 'use, check the history first, then step down:'
echo '  docker compose --profile nuc run --rm api alembic history'
echo '  docker compose --profile nuc run --rm api alembic downgrade -1'
