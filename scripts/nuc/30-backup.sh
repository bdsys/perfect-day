#!/usr/bin/env bash
# scripts/nuc/30-backup.sh — install the nightly backup (tracker row 36).
# Run on the NUC, in your own terminal (it shows the backup's private key once):
#   sudo /opt/perfect-day/scripts/nuc/30-backup.sh
#
# - Makes the backup keypair (/etc/perfect-day/backup.key + backup.pub) if there is
#   none, shows the private key once, and asks you to type "saved" once it is in your
#   password manager. Store MASTER_SECRET from /etc/perfect-day/app.env there too.
# - Locks down /var/backups/perfect-day (700, files 600).
# - Installs perfect-day-backup.service (runs scripts/nuc/backup-run.sh) and
#   perfect-day-backup.timer (daily 03:00 Pacific), and enables the timer. It never starts a
#   backup itself: run one with `sudo systemctl start perfect-day-backup.service`.
# Safe to run again: an existing key is kept and not shown.
# Design: docs/superpowers/specs/2026-11-18-nuc-backups-design.md
set -euo pipefail
umask 077

if [ "$(id -u)" -ne 0 ] && [ -z "${PD_INSTALL_ALLOW_NONROOT:-}" ]; then
    echo "ERROR: run as root (use sudo)." >&2
    exit 1
fi

# shellcheck source=lib/backup.sh
source "$(dirname "$(readlink -f "$0")")/lib/backup.sh"
PD_SYSTEMD_DIR="${PD_SYSTEMD_DIR:-/etc/systemd/system}"

echo "=== Perfect Day backup install ==="

echo "[1/4] Locking down ${PD_BACKUP_ROOT}"
mkdir -p "${PD_NIGHTLY}" "${PD_LOG_DIR}"
chmod 700 "${PD_BACKUP_ROOT}" "${PD_NIGHTLY}"
find "${PD_BACKUP_ROOT}" -maxdepth 1 -type f -exec chmod 600 {} +

echo "[2/4] Backup key"
if [ -f "${PD_KEY}" ]; then
    echo "  Keeping the existing key (${PD_KEY}); it is not shown again."
    age-keygen -y "${PD_KEY}" > "${PD_PUB}"
else
    age-keygen -o "${PD_KEY}" 2>/dev/null
    chmod 600 "${PD_KEY}"
    age-keygen -y "${PD_KEY}" > "${PD_PUB}"
    cat <<MSG

  ┌──────────────────────────────────────────────────────────────────────┐
  │ SAVE THIS PRIVATE KEY IN YOUR PASSWORD MANAGER NOW.                  │
  │ Every backup is encrypted to it; without it no backup can be read.   │
  │ It is shown only this once.                                          │
  └──────────────────────────────────────────────────────────────────────┘

$(grep '^AGE-SECRET-KEY-' "${PD_KEY}")

  Also save MASTER_SECRET from ${PD_ETC}/app.env as a second entry: photos and
  Google tokens are encrypted with it. (The backups contain it too, but a
  separate copy is the safety net.)

MSG
    printf 'Type "saved" once both are in your password manager: '
    read -r answer || true
    if [ "${answer:-}" != "saved" ]; then
        rm -f "${PD_KEY}" "${PD_PUB}"
        echo ""
        echo "Not confirmed. The key was removed; run this again to make and show a new one."
        exit 1
    fi
fi
chmod 600 "${PD_KEY}"
chmod 644 "${PD_PUB}"

echo "[3/4] Installing the service and timer"
cat > "${PD_SYSTEMD_DIR}/perfect-day-backup.service" <<UNIT
[Unit]
Description=Perfect Day nightly backup (database, photos, config) to the NAS
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${PD_DEPLOY_DIR}/scripts/nuc/backup-run.sh
TimeoutStartSec=15min
Nice=10
IOSchedulingClass=idle
UNIT

cat > "${PD_SYSTEMD_DIR}/perfect-day-backup.timer" <<UNIT
[Unit]
Description=Perfect Day nightly backup at 03:00 Pacific

[Timer]
# Andrew's local time (2026-11-19); systemd follows daylight saving.
OnCalendar=*-*-* 03:00:00 America/Los_Angeles
Persistent=true
Unit=perfect-day-backup.service

[Install]
WantedBy=timers.target
UNIT

echo "[4/4] Enabling the timer"
systemctl daemon-reload
systemctl enable --now perfect-day-backup.timer

cat <<MSG

Backup installed. The first run is at 03:00 Pacific, or start one now:
  sudo systemctl start perfect-day-backup.service
  cat ${PD_LOG_DIR}/backup-status.json
Then prove it restores (deploy/nuc-ops.md § B7):
  sudo ${PD_DEPLOY_DIR}/scripts/nuc/drill.sh
MSG
