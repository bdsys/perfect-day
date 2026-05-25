#!/usr/bin/env bash
# scripts/nuc/20-deploy.sh — First deploy (or full redeploy) to the NUC
# Run on the NUC as: sudo ./scripts/nuc/20-deploy.sh [--clean]
#   --clean  Wipe all Docker volumes before deploying (required after re-running 10-secrets.sh,
#            or after a partial teardown). Covers --profile nuc AND --profile dev so pgadmin
#            containers from stray dev invocations don't block the volume wipe.
#            The wipe runs AFTER git pull so compose sees the current YAML.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

CLEAN=false
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

REPO_URL="git@github.com:bdsys/perfect-day.git"
DEPLOY_DIR="/opt/perfect-day"
ENV_FILE="/etc/perfect-day/app.env"
HEALTH_URL="https://api.diary.perfectday.andrewlass.com/readyz"
HEALTH_TIMEOUT=90

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/deploy-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Perfect Day First Deploy ==="
echo "Deploy dir: ${DEPLOY_DIR}"
echo "Date: $(date)"
echo ""

echo '[1/9] Cloning or updating repository...'
# /opt/perfect-day is owned by perfectday:docker (per 00-bootstrap.sh) but this
# script runs as root. Tell git the directory is safe to avoid "dubious ownership".
git config --global --add safe.directory "${DEPLOY_DIR}"
if [ -d "${DEPLOY_DIR}/.git" ]; then
    cd "${DEPLOY_DIR}"
    git pull --ff-only
    echo '  Updated existing repo'
else
    if ! git clone "${REPO_URL}" "${DEPLOY_DIR}"; then
        echo "" >&2
        echo "ERROR: git clone failed." >&2
        echo "Ensure a deploy key is installed at /root/.ssh/id_ed25519 and its" >&2
        echo "public key is added to the repository as a deploy key on GitHub." >&2
        exit 1
    fi
    cd "${DEPLOY_DIR}"
    echo '  Cloned fresh repo'
fi

# ── --clean: wipe all volumes AFTER git pull so compose has the current YAML ──
# This must run before the secrets symlink and before pulling/building images,
# because Postgres only honors POSTGRES_PASSWORD on first init — if the data
# volume survives a secrets regeneration the passwords drift and auth fails.
if [ "${CLEAN}" = true ]; then
    echo '[2/9] Wiping Docker volumes (--clean)...'
    echo '  WARNING: Wiping all perfect-day_* volumes. Postgres data will be lost.'
    cd "${DEPLOY_DIR}"

    # Stop containers across BOTH profiles: --profile nuc (api, worker, beat, web, edge, ddns)
    # and --profile dev (pgadmin) — pgadmin is dev-only but gets left running when the NUC
    # was accidentally brought up with `make up` or `docker compose up` without --profile nuc.
    docker compose --profile nuc --profile dev down -v --remove-orphans 2>/dev/null || true

    # Backstop: kill any container still holding a reference to a perfect-day_* volume
    # (e.g. a container started with `docker run` outside of compose).
    for vol in $(docker volume ls --format '{{.Name}}' | grep '^perfect-day_' || true); do
        container_ids=$(docker ps -a --filter volume="${vol}" --format '{{.ID}}' || true)
        if [ -n "${container_ids}" ]; then
            echo "  Force-removing container(s) still holding ${vol}: ${container_ids}"
            echo "${container_ids}" | xargs -r docker rm -f
        fi
    done

    # Now every volume referencing container is gone — remove the volumes.
    vols=$(docker volume ls --format '{{.Name}}' | grep '^perfect-day_' || true)
    if [ -n "${vols}" ]; then
        echo "${vols}" | xargs docker volume rm
        echo "  Volumes removed: $(echo "${vols}" | tr '\n' ' ')"
    else
        echo "  No perfect-day_* volumes found (already clean)."
    fi
else
    echo '[2/9] Skipping volume wipe (no --clean flag).'
fi

echo '[3/9] Linking secrets file...'
if [ ! -f "${ENV_FILE}" ]; then
    echo "ERROR: ${ENV_FILE} not found. Run scripts/nuc/10-secrets.sh first." >&2
    exit 1
fi
# Compose reads ./.env for ${VAR} interpolation (postgres/minio creds);
# api/worker/beat services consume ./apps/api/.env via env_file. Symlink both
# to the same source-of-truth secrets file.
ln -sf "${ENV_FILE}" "${DEPLOY_DIR}/.env"
mkdir -p "${DEPLOY_DIR}/apps/api"
ln -sf "${ENV_FILE}" "${DEPLOY_DIR}/apps/api/.env"
echo "  Linked ${ENV_FILE} -> ${DEPLOY_DIR}/.env"
echo "  Linked ${ENV_FILE} -> ${DEPLOY_DIR}/apps/api/.env"

echo '[4/9] Rendering Caddyfile from template...'
FORTIGATE_LAN_IP=$(grep -E '^FORTIGATE_LAN_IP=' "${DEPLOY_DIR}/.env" | cut -d= -f2-)
export FORTIGATE_LAN_IP
"${DEPLOY_DIR}/scripts/nuc/render-caddyfile.sh"

echo '[5/9] Pulling Docker images...'
cd "${DEPLOY_DIR}"
# Try GHCR first; fall back to local build if images not yet pushed
if docker compose --profile nuc pull api worker beat web edge 2>/dev/null; then
    echo '  Pulled from GHCR'
else
    echo '  Images not on GHCR yet — building locally (first deploy)'
    docker compose --profile nuc build api web
fi

echo '[6/9] Running Alembic migrations...'
docker compose --profile nuc run --rm api alembic upgrade head
echo '  Migrations complete'

echo '[7/9] Starting all services...'
docker compose --profile nuc up -d
echo '  Services started'

echo '[8/9] Seeding MinIO bucket...'
./scripts/seed-minio-bucket.sh || true

echo '[9/9] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 "${HEALTH_URL}" > /dev/null 2>&1; do
    if [ "${ELAPSED}" -ge "${HEALTH_TIMEOUT}" ]; then
        echo "ERROR: Service not healthy after ${HEALTH_TIMEOUT}s" >&2
        docker compose logs --tail=50
        exit 1
    fi
    sleep 5
    ELAPSED=$(( ELAPSED + 5 ))
    echo "  Waiting... ${ELAPSED}s"
done

SHA=$(git rev-parse --short HEAD)
echo "${SHA}" > "${DEPLOY_DIR}/last-deployed-sha"

# Re-enable systemd so the stack auto-starts on next NUC reboot.
# We only `enable`, not `start` — services are already running from the compose up above.
if systemctl list-unit-files 2>/dev/null | grep -q '^perfect-day\.service'; then
    systemctl enable perfect-day.service >/dev/null 2>&1 || true
    echo '  Systemd perfect-day.service re-enabled (auto-start on reboot).'
fi

echo ''
echo '╔══════════════════════════════════════════════════════════════════╗'
echo "║  Deploy complete: sha-${SHA}"
echo '╚══════════════════════════════════════════════════════════════════╝'
echo "Log: ${LOG_FILE}"
