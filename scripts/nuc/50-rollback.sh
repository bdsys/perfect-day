#!/usr/bin/env bash
# scripts/nuc/50-rollback.sh — Roll back to a previous image SHA
# Usage: ./scripts/nuc/50-rollback.sh [user@host] [sha]
# Example: ./scripts/nuc/50-rollback.sh perfectday@192.168.1.100
#          ./scripts/nuc/50-rollback.sh perfectday@192.168.1.100 abc1234
# Without a SHA, reads last-deployed-sha from the deploy dir.
set -euo pipefail

REMOTE="${1:-perfectday@localhost}"
TARGET_SHA="${2:-}"
DEPLOY_DIR="/opt/perfect-day"
HEALTH_URL="https://api.diary.perfectday.bdsys.net/readyz"
HEALTH_TIMEOUT=90
GHCR_OWNER="andrewlass"

echo "=== Perfect Day Rollback ==="
echo "Target: ${REMOTE}"
echo ""

ssh_or_local() {
    if [[ "${REMOTE}" == "perfectday@localhost" || "${REMOTE}" == "localhost" ]]; then
        bash -c "$*"
    else
        ssh -o StrictHostKeyChecking=accept-new "${REMOTE}" "$@"
    fi
}

# Resolve SHA on the remote host
if [ -z "${TARGET_SHA}" ]; then
    TARGET_SHA=$(ssh_or_local "cat '${DEPLOY_DIR}/last-deployed-sha' 2>/dev/null || echo ''")
    if [ -z "${TARGET_SHA}" ]; then
        echo "ERROR: No SHA provided and ${DEPLOY_DIR}/last-deployed-sha not found." >&2
        echo "Usage: $0 [user@host] <sha>" >&2
        exit 1
    fi
    echo "Rolling back to last recorded SHA: ${TARGET_SHA}"
else
    echo "Rolling back to specified SHA: ${TARGET_SHA}"
fi

echo ""

ssh_or_local "
set -euo pipefail

DEPLOY_DIR='${DEPLOY_DIR}'
TARGET_SHA='${TARGET_SHA}'
HEALTH_URL='${HEALTH_URL}'
GHCR_OWNER='${GHCR_OWNER}'
LOG_DIR=/var/log/perfect-day
LOG_FILE=\"\${LOG_DIR}/rollback-\$(date +%Y%m%d-%H%M%S).log\"

mkdir -p \"\${LOG_DIR}\"
exec > >(tee -a \"\${LOG_FILE}\") 2>&1

echo \"=== Rollback to sha-\${TARGET_SHA} ===\"
cd \"\${DEPLOY_DIR}\"

echo '[1/5] Overriding image tags in compose...'
# Pull tagged images from GHCR
docker pull \"ghcr.io/\${GHCR_OWNER}/perfect-day-api:sha-\${TARGET_SHA}\" || {
    echo 'ERROR: Image sha-${TARGET_SHA} not found in GHCR.' >&2
    echo 'Available tags: docker image ls ghcr.io/${GHCR_OWNER}/perfect-day-api' >&2
    exit 1
}
docker pull \"ghcr.io/\${GHCR_OWNER}/perfect-day-web:sha-\${TARGET_SHA}\"

echo '[2/5] Tagging rollback images as latest locally...'
docker tag \"ghcr.io/\${GHCR_OWNER}/perfect-day-api:sha-\${TARGET_SHA}\" \"ghcr.io/\${GHCR_OWNER}/perfect-day-api:latest\"
docker tag \"ghcr.io/\${GHCR_OWNER}/perfect-day-web:sha-\${TARGET_SHA}\" \"ghcr.io/\${GHCR_OWNER}/perfect-day-web:latest\"

echo '[3/5] Restarting app services with rolled-back images...'
docker compose up -d --no-deps api worker beat web
echo '  Services restarted'

echo '[4/5] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 \"\${HEALTH_URL}\" > /dev/null 2>&1; do
    if [ \"\${ELAPSED}\" -ge '${HEALTH_TIMEOUT}' ]; then
        echo 'ERROR: Rollback target also unhealthy after ${HEALTH_TIMEOUT}s.' >&2
        echo 'Check service logs: docker compose logs --tail=100' >&2
        exit 1
    fi
    sleep 5
    ELAPSED=\$(( ELAPSED + 5 ))
    echo \"  Waiting... \${ELAPSED}s\"
done

echo \"[5/5] Rollback complete: sha-\${TARGET_SHA}\"
echo \"Log: \${LOG_FILE}\"
echo ''
echo 'NOTE: Database migrations are NOT automatically rolled back.'
echo 'If the new release ran a forward migration that is incompatible with the'
echo 'rolled-back code, run manually:'
echo '  docker compose run --rm api alembic downgrade -1'
echo 'Verify alembic history first: docker compose run --rm api alembic history'
"
