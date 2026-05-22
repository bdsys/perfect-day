#!/usr/bin/env bash
# scripts/nuc/40-update.sh — Pull new images, migrate, restart app services
# Usage: ./scripts/nuc/40-update.sh [user@host] [sha]
# Example: ./scripts/nuc/40-update.sh perfectday@192.168.1.100
#          ./scripts/nuc/40-update.sh perfectday@192.168.1.100 abc1234
# Defaults to perfectday@localhost (run locally on the NUC).
set -euo pipefail

REMOTE="${1:-perfectday@localhost}"
TARGET_SHA="${2:-}"
DEPLOY_DIR="/opt/perfect-day"
HEALTH_URL="https://api.diary.perfectday.bdsys.net/readyz"
HEALTH_TIMEOUT=90
SMOKE_SCRIPT="./scripts/smoke-test.sh"

echo "=== Perfect Day Update ==="
echo "Target: ${REMOTE}"
[ -n "${TARGET_SHA}" ] && echo "SHA:    ${TARGET_SHA}" || echo "SHA:    HEAD (latest)"
echo ""

ssh_or_local() {
    if [[ "${REMOTE}" == "perfectday@localhost" || "${REMOTE}" == "localhost" ]]; then
        bash -c "$*"
    else
        ssh -o StrictHostKeyChecking=accept-new "${REMOTE}" "$@"
    fi
}

ssh_or_local "
set -euo pipefail

DEPLOY_DIR='${DEPLOY_DIR}'
TARGET_SHA='${TARGET_SHA}'
HEALTH_URL='${HEALTH_URL}'
LOG_DIR=/var/log/perfect-day
LOG_FILE=\"\${LOG_DIR}/update-\$(date +%Y%m%d-%H%M%S).log\"

mkdir -p \"\${LOG_DIR}\"
exec > >(tee -a \"\${LOG_FILE}\") 2>&1

cd \"\${DEPLOY_DIR}\"

echo '[1/6] Updating repository...'
git fetch origin
if [ -n \"\${TARGET_SHA}\" ]; then
    git checkout \"\${TARGET_SHA}\"
    echo \"  Checked out sha: \${TARGET_SHA}\"
else
    git pull --ff-only
    echo \"  Updated to: \$(git rev-parse --short HEAD)\"
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
until curl -sf --max-time 5 \"\${HEALTH_URL}\" > /dev/null 2>&1; do
    if [ \"\${ELAPSED}\" -ge '${HEALTH_TIMEOUT}' ]; then
        echo 'ERROR: Service not healthy after ${HEALTH_TIMEOUT}s — rolling back.' >&2
        echo 'Run: ./scripts/nuc/50-rollback.sh ${REMOTE}' >&2
        exit 1
    fi
    sleep 5
    ELAPSED=\$(( ELAPSED + 5 ))
    echo \"  Waiting... \${ELAPSED}s\"
done

SHA=\$(git rev-parse --short HEAD)
echo \"\${SHA}\" > \"\${DEPLOY_DIR}/last-deployed-sha\"
echo \"  Recorded deployed SHA: \${SHA}\"

echo '[6/6] Update complete.'
echo \"Log: \${LOG_FILE}\"
"

echo ""
echo "Running smoke test against ${HEALTH_URL%/readyz}..."
if "${SMOKE_SCRIPT}" "${HEALTH_URL%/readyz}" 2>/dev/null; then
    echo "Smoke test passed."
else
    echo "WARNING: Smoke test failed. Check logs or run rollback:"
    echo "  ./scripts/nuc/50-rollback.sh ${REMOTE}"
    exit 1
fi
