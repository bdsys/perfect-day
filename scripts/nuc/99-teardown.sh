#!/usr/bin/env bash
# scripts/nuc/99-teardown.sh — Full nuke of all Perfect Day state on the NUC
#
# Removes EVERYTHING. You must re-run 10-secrets.sh and 20-deploy.sh afterward.
#
# What gets removed:
#   - perfect-day.service systemd unit (stopped + disabled; unit file stays)
#   - All Docker containers started by this project (across --profile nuc AND --profile dev)
#   - All perfect-day_* named Docker volumes
#   - /etc/perfect-day/app.env  (secrets — forces 10-secrets.sh re-run)
#   - /etc/perfect-day/cloudflare-ddns.config.json  (re-prompted by 10-secrets.sh)
#   - /opt/perfect-day  (the deployed repo — re-cloned by 20-deploy.sh)
#
# What stays:
#   - The perfectday OS user, andrew's docker group membership, UFW, fail2ban
#   - /etc/perfect-day/ directory (permissions intact for 10-secrets.sh)
#   - /var/log/perfect-day/ and existing log files
#   - Docker images (harmless cached layers; delete manually with docker image prune -a)
#   - perfect-day-backup.timer (disable manually before teardown if backups are in flight)
#
# Usage:
#   sudo ./scripts/nuc/99-teardown.sh --yes
#
# Run without --yes to see a dry-run summary of what will be destroyed.
#
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

YES=false
for arg in "$@"; do
    case "$arg" in
        --yes) YES=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

# ── Dry-run summary ──────────────────────────────────────────────────────────
if [ "${YES}" = false ]; then
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  Perfect Day — Full Teardown (DRY RUN)                          ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "This script will PERMANENTLY DESTROY:"
    echo "  1. Stop + disable systemd perfect-day.service"
    echo "  2. docker compose --profile nuc --profile dev down -v (all containers + volumes)"
    echo "  3. Force-remove any lingering containers referencing perfect-day_* volumes"
    echo "  4. docker volume rm all perfect-day_* volumes"
    echo "  5. rm /etc/perfect-day/app.env  (you will need to re-enter all API keys)"
    echo "  6. rm /etc/perfect-day/cloudflare-ddns.config.json"
    echo "  7. rm -rf /opt/perfect-day  (the deployed repo)"
    echo ""
    echo "After teardown, re-run in order:"
    echo "  sudo ./scripts/nuc/10-secrets.sh   (re-prompts for all API keys)"
    echo "  sudo ./scripts/nuc/20-deploy.sh    (re-clones repo + starts services)"
    echo ""
    echo "To proceed, run:  sudo $0 --yes"
    exit 0
fi

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/teardown-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Perfect Day — Full Teardown                                     ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo "Date: $(date)"
echo ""

# ── Step 1: Stop + disable systemd unit ──────────────────────────────────────
echo "[1/6] Stopping and disabling perfect-day.service..."
if systemctl list-unit-files 2>/dev/null | grep -q '^perfect-day\.service'; then
    systemctl stop perfect-day.service 2>/dev/null || true
    systemctl disable perfect-day.service 2>/dev/null || true
    echo "  Stopped and disabled."
else
    echo "  Unit not found — skipping."
fi

# ── Step 2: Docker compose down across all profiles ──────────────────────────
echo "[2/6] Stopping all containers (--profile nuc --profile dev)..."
if [ -d /opt/perfect-day ]; then
    cd /opt/perfect-day
    # Suppress errors — compose complains if there's nothing to stop, and that's fine.
    docker compose --profile nuc --profile dev down -v --remove-orphans 2>/dev/null || true
    echo "  Done."
else
    echo "  /opt/perfect-day not found — skipping compose down."
fi

# ── Step 3: Container backstop ───────────────────────────────────────────────
# Catches containers that were started outside compose (e.g. docker run) that
# still hold a reference to a perfect-day_* volume, which would block volume rm.
echo "[3/6] Force-removing any remaining containers referencing perfect-day_* volumes..."
FOUND=false
for vol in $(docker volume ls --format '{{.Name}}' | grep '^perfect-day_' || true); do
    container_ids=$(docker ps -a --filter volume="${vol}" --format '{{.ID}}' || true)
    if [ -n "${container_ids}" ]; then
        echo "  Volume ${vol} still referenced by: ${container_ids}"
        echo "${container_ids}" | xargs -r docker rm -f
        FOUND=true
    fi
done
if [ "${FOUND}" = false ]; then
    echo "  No lingering containers found."
fi

# ── Step 4: Remove named volumes ─────────────────────────────────────────────
echo "[4/6] Removing perfect-day_* Docker volumes..."
vols=$(docker volume ls --format '{{.Name}}' | grep '^perfect-day_' || true)
if [ -n "${vols}" ]; then
    echo "${vols}" | xargs docker volume rm
    echo "  Removed: $(echo "${vols}" | tr '\n' ' ')"
else
    echo "  No perfect-day_* volumes found."
fi

# ── Step 5: Remove secrets + config files ────────────────────────────────────
echo "[5/6] Removing /etc/perfect-day/app.env and cloudflare-ddns.config.json..."
removed=false
if [ -f /etc/perfect-day/app.env ]; then
    rm -f /etc/perfect-day/app.env
    echo "  Removed /etc/perfect-day/app.env"
    removed=true
fi
if [ -f /etc/perfect-day/cloudflare-ddns.config.json ]; then
    rm -f /etc/perfect-day/cloudflare-ddns.config.json
    echo "  Removed /etc/perfect-day/cloudflare-ddns.config.json"
    removed=true
fi
if [ "${removed}" = false ]; then
    echo "  Files already absent — nothing to remove."
fi

# ── Step 6: Remove deployed repo ─────────────────────────────────────────────
echo "[6/6] Removing /opt/perfect-day..."
if [ -d /opt/perfect-day ]; then
    rm -rf /opt/perfect-day
    echo "  Removed."
else
    echo "  Already absent."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Teardown complete.                                              ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps (run in order):"
echo "  sudo git clone git@github.com:bdsys/perfect-day.git /opt/perfect-day"
echo "  cd /opt/perfect-day"
echo "  sudo ./scripts/nuc/10-secrets.sh   # re-prompts for all 4 API keys"
echo "  sudo ./scripts/nuc/20-deploy.sh    # re-clones repo + starts services"
echo ""
echo "Log: ${LOG_FILE}"
