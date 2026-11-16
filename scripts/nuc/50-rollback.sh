#!/usr/bin/env bash
# scripts/nuc/50-rollback.sh — Go back one deploy
# Usage: sudo ./scripts/nuc/50-rollback.sh
#
# 40-update.sh tags the running app images `previous` before it builds a new commit,
# and records that commit in previous-deployed-sha. This script puts those images
# back, checks out that commit (so the compose file matches the images) and restarts.
# It goes back exactly one deploy; the next 40-update.sh returns to main.
#
# It keeps the current Caddyfile and /etc/perfect-day/app.env: those follow the
# public hostnames, not the code, and an older template could route names that no
# longer exist.
#
# Database migrations are NOT rolled back. If the newer release migrated the schema
# and the older code cannot run on it, downgrade BEFORE rolling back, while the newer
# image (which has the newer migration files) is still :latest:
#   ls via: git ls-tree --name-only <previous sha> apps/api/alembic/versions/
#   docker compose --profile nuc run --rm api alembic downgrade <that commit's head revision>
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

echo '[1/4] Checking out the previous commit (Caddyfile and app.env are kept)...'
git checkout -q "${PREV_SHA}"

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
echo 'NOTE: Database migrations are NOT rolled back. If the older code fails on the'
echo 'newer schema, the downgrade has to run with the NEWER image (only it has the newer'
echo 'migration files): run 40-update.sh again to return to it, then'
echo "  docker compose --profile nuc run --rm api alembic downgrade <head revision of ${PREV_SHA}>"
echo 'and roll back again. See the header of this script.'
