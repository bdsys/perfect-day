#!/usr/bin/env bash
# scripts/nuc/40-update.sh — Deploy the latest code: build images on the NUC, migrate, restart
# Usage: sudo ./scripts/nuc/40-update.sh [sha]
# Example: sudo ./scripts/nuc/40-update.sh            # latest main
#          sudo ./scripts/nuc/40-update.sh abc1234    # a specific commit
#
# The app images (api, worker, beat, web) are built here from the checked-out code;
# compose defines them with `build:` and no registry image, so there is nothing to pull.
# Before building a new commit, the running images are tagged `previous` so
# 50-rollback.sh can go back one deploy. CI-built images pulled on a timer are the
# planned replacement (PoC 25.4, finding P-2).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

# Step 1 pulls a new copy of this script; carry on with that copy (lib/self-update.sh).
# shellcheck source=lib/self-update.sh
source "$(dirname "$(readlink -f "$0")")/lib/self-update.sh"
self_update_snapshot

# Never deploy while the nightly backup runs, or run two deploys at once
# (lib/deploy-lock.sh). Waits up to 30 minutes for a running backup.
# shellcheck source=lib/deploy-lock.sh
source "$(dirname "$(readlink -f "$0")")/lib/deploy-lock.sh"
if ! deploy_lock_take; then
    echo "ERROR: a backup has held the lock for 30 minutes; not deploying." >&2
    exit 1
fi

TARGET_SHA="${1:-}"
DEPLOY_DIR="/opt/perfect-day"
HEALTH_URL="https://perfectday-api.andrewlass.com/readyz"
HEALTH_TIMEOUT=90
SMOKE_SCRIPT="./scripts/smoke-test.sh"

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/update-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}" 8>&-) 2>&1  # tee must not hold the deploy lock

echo "=== Perfect Day Update ==="
[ -n "${TARGET_SHA}" ] && echo "SHA:    ${TARGET_SHA}" || echo "SHA:    HEAD (latest)"
echo ""

cd "${DEPLOY_DIR}"

IMAGE_PREFIX="perfect-day"   # compose project name (the deploy directory's name)
APP_SERVICES="api worker beat web"
PREV_FILE="${DEPLOY_DIR}/previous-deployed-sha"
DDNS_CONFIG="/etc/perfect-day/cloudflare-ddns.config.json"
# Full shas are recorded; an older short one (from earlier scripts) is expanded here.
DEPLOYED_SHA=$(cat "${DEPLOY_DIR}/last-deployed-sha" 2>/dev/null || echo '')
if [ -n "${DEPLOYED_SHA}" ]; then
    DEPLOYED_SHA=$(git rev-parse --verify -q "${DEPLOYED_SHA}^{commit}" || echo "${DEPLOYED_SHA}")
fi

echo '[1/8] Updating repository...'
git fetch origin
if [ -n "${TARGET_SHA}" ]; then
    git checkout -q "${TARGET_SHA}"
    echo "  Checked out sha: ${TARGET_SHA}"
else
    git checkout -q main    # leaves a detached HEAD from a rollback or a sha deploy
    git pull --ff-only
    echo "  Updated to: $(git rev-parse --short HEAD)"
fi
NEW_SHA=$(git rev-parse HEAD)
# If the pull changed this script, run the new copy from the top. Everything above is
# safe to repeat: a second log file starts, and the checkout or pull is a no-op.
self_update_rerun_if_changed "$@"

echo '[2/8] Rendering Caddyfile from template...'
ENV_FILE="/etc/perfect-day/app.env"
if [ -f "${ENV_FILE}" ]; then
    FORTIGATE_LAN_IP=$(grep -E '^FORTIGATE_LAN_IP=' "${ENV_FILE}" | cut -d= -f2-)
    export FORTIGATE_LAN_IP
else
    echo "  WARNING: ${ENV_FILE} not found — Caddyfile will use private_ranges fallback." >&2
fi
./scripts/nuc/render-caddyfile.sh

echo '[3/8] Keeping the running images for rollback...'
previous_complete() {
    for svc in ${APP_SERVICES}; do
        docker image inspect "${IMAGE_PREFIX}-${svc}:previous" > /dev/null 2>&1 || return 1
    done
}
if [ -z "${DEPLOYED_SHA}" ] || [ "${DEPLOYED_SHA}" = "${NEW_SHA}" ]; then
    echo "  Same commit as deployed (or none recorded); :previous left as it is"
elif [ "$(cat "${PREV_FILE}" 2>/dev/null || echo '')" = "${DEPLOYED_SHA}" ] && previous_complete; then
    # An earlier attempt at this update failed after tagging. The running containers
    # may already be the new build, so do not tag them over the good images.
    echo "  :previous already holds ${DEPLOYED_SHA} from an earlier attempt; keeping it"
else
    # Tag what is running, not :latest: after a failed build, :latest can be a new image.
    for svc in ${APP_SERVICES}; do
        CID=$(docker compose --profile nuc ps -q "${svc}")
        if [ -z "${CID}" ]; then
            echo "ERROR: ${svc} is not running, so there is nothing to keep for a rollback." >&2
            echo "  Start the stack first (docker compose --profile nuc up -d), or deploy" >&2
            echo "  without a rollback copy by removing ${DEPLOY_DIR}/last-deployed-sha." >&2
            exit 1
        fi
        docker tag "$(docker inspect -f '{{.Image}}' "${CID}")" "${IMAGE_PREFIX}-${svc}:previous"
    done
    echo "${DEPLOYED_SHA}" > "${PREV_FILE}"
    echo "  Tagged the running images (${DEPLOYED_SHA}) as :previous"
fi

echo '[4/8] Building app images...'
# shellcheck disable=SC2086
docker compose --profile nuc build ${APP_SERVICES}
docker compose --profile nuc pull edge

echo '[5/8] Running migrations...'
# The worker and beat would otherwise run old code against a half-changed schema.
docker compose --profile nuc stop worker beat
if ! docker compose --profile nuc run --rm api alembic upgrade head; then
    echo "ERROR: migrations failed. Each migration runs in one transaction, so the" >&2
    echo "  database is unchanged, but :latest now holds the new images, which a reboot" >&2
    echo "  would start on the old schema. Run now: sudo ./scripts/nuc/50-rollback.sh" >&2
    exit 1
fi
echo '  Migrations complete'

echo '[6/8] Restarting app services (infra stays up)...'
# shellcheck disable=SC2086
docker compose --profile nuc up -d --no-deps ${APP_SERVICES}
# Caddy and the DDNS updater read mounted files that compose cannot see change.
docker compose --profile nuc up -d --no-deps --force-recreate edge
if [ -f "${DDNS_CONFIG}" ]; then
    docker compose --profile nuc up -d --no-deps --force-recreate cloudflare-ddns
fi
echo '  Services restarted'

echo '[7/8] Waiting for readiness...'
ELAPSED=0
until curl -sf --max-time 5 "${HEALTH_URL}" > /dev/null 2>&1; do
    if [ "${ELAPSED}" -ge "${HEALTH_TIMEOUT}" ]; then
        echo "ERROR: Service not healthy after ${HEALTH_TIMEOUT}s." >&2
        echo 'To go back to the previous deploy: sudo ./scripts/nuc/50-rollback.sh' >&2
        exit 1
    fi
    sleep 5
    ELAPSED=$(( ELAPSED + 5 ))
    echo "  Waiting... ${ELAPSED}s"
done

echo "${NEW_SHA}" > "${DEPLOY_DIR}/last-deployed-sha"
echo "  Recorded deployed SHA: ${NEW_SHA}"

echo '[8/8] Update complete.'
echo "Log: ${LOG_FILE}"

echo ""
echo "Running smoke test against ${HEALTH_URL%/readyz}..."
# SMOKE_CLEANUP=1: this runs beside the database, so the smoke test deletes its accounts.
if SMOKE_CLEANUP=1 "${SMOKE_SCRIPT}" "${HEALTH_URL%/readyz}" 2>/dev/null; then
    echo "Smoke test passed."
else
    echo "WARNING: Smoke test failed. Check logs or run rollback:"
    echo "  sudo ./scripts/nuc/50-rollback.sh"
    exit 1
fi
