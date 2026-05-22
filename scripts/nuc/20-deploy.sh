#!/usr/bin/env bash
# scripts/nuc/20-deploy.sh — First deploy (or full redeploy) to the NUC
# Usage: ./scripts/nuc/20-deploy.sh [user@host]
# Example: ./scripts/nuc/20-deploy.sh perfectday@192.168.1.100
# Defaults to perfectday@localhost if no argument given (run locally on the NUC).
set -euo pipefail

REMOTE="${1:-perfectday@localhost}"
REPO_URL="https://github.com/andrewlass/perfect-day.git"
DEPLOY_DIR="/opt/perfect-day"
ENV_FILE="/etc/perfect-day/app.env"
HEALTH_URL="https://api.diary.perfectday.bdsys.net/readyz"
HEALTH_TIMEOUT=90

echo "=== Perfect Day First Deploy ==="
echo "Target: ${REMOTE}"
echo "Deploy dir: ${DEPLOY_DIR}"
echo ""

# Run the deploy steps on the remote host (or locally if REMOTE is localhost)
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
ENV_FILE='${ENV_FILE}'
LOG_DIR=/var/log/perfect-day
LOG_FILE=\"\${LOG_DIR}/deploy-\$(date +%Y%m%d-%H%M%S).log\"

mkdir -p \"\${LOG_DIR}\"
exec > >(tee -a \"\${LOG_FILE}\") 2>&1

echo '[1/7] Cloning or updating repository...'
if [ -d \"\${DEPLOY_DIR}/.git\" ]; then
    cd \"\${DEPLOY_DIR}\"
    git pull --ff-only
    echo '  Updated existing repo'
else
    git clone '${REPO_URL}' \"\${DEPLOY_DIR}\"
    cd \"\${DEPLOY_DIR}\"
    echo '  Cloned fresh repo'
fi

echo '[2/7] Linking secrets file...'
if [ ! -f '${ENV_FILE}' ]; then
    echo 'ERROR: ${ENV_FILE} not found. Run scripts/nuc/10-secrets.sh first.' >&2
    exit 1
fi
# Symlink so docker-compose.yml finds .env in the project root
ln -sf '${ENV_FILE}' \"\${DEPLOY_DIR}/.env\"
echo '  Linked ${ENV_FILE} -> \${DEPLOY_DIR}/.env'

echo '[3/7] Pulling Docker images...'
cd \"\${DEPLOY_DIR}\"
# Try GHCR first; fall back to local build if images not yet pushed
if docker compose pull api worker beat web 2>/dev/null; then
    echo '  Pulled from GHCR'
else
    echo '  Images not on GHCR yet — building locally (first deploy)'
    docker compose build api web
fi

echo '[4/7] Running Alembic migrations...'
docker compose run --rm api alembic upgrade head
echo '  Migrations complete'

echo '[5/7] Starting all services...'
docker compose up -d
echo '  Services started'

echo '[6/7] Seeding MinIO bucket...'
./scripts/seed-minio-bucket.sh || true

echo '[7/7] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 '${HEALTH_URL}' > /dev/null 2>&1; do
    if [ \"\${ELAPSED}\" -ge '${HEALTH_TIMEOUT}' ]; then
        echo 'ERROR: Service not healthy after ${HEALTH_TIMEOUT}s' >&2
        docker compose logs --tail=50
        exit 1
    fi
    sleep 5
    ELAPSED=\$(( ELAPSED + 5 ))
    echo \"  Waiting... \${ELAPSED}s\"
done

SHA=\$(git rev-parse --short HEAD)
echo \"\${SHA}\" > \"\${DEPLOY_DIR}/last-deployed-sha\"

echo ''
echo '╔══════════════════════════════════════════╗'
echo \"║  Deploy complete: sha-\${SHA}\"
echo '╚══════════════════════════════════════════╝'
echo \"Log: \${LOG_FILE}\"
"
