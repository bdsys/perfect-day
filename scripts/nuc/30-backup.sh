#!/usr/bin/env bash
# scripts/nuc/30-backup.sh — Provision daily encrypted backup with rclone → B2
# Run as root on the NUC after first deploy.
# Creates an age keypair, backup service + systemd timer.
set -euo pipefail

SECRETS_DIR=/etc/perfect-day
AGE_KEY="${SECRETS_DIR}/backup.key"
AGE_PUB="${SECRETS_DIR}/backup.pub"
BACKUP_DIR=/var/backups/perfect-day
LOG_DIR=/var/log/perfect-day
KEEP_DAYS=7
TIMER_HOUR=2
TIMER_MIN=17  # off the :00 mark

echo "=== Perfect Day Backup Provisioning ==="
echo ""

# ── 1. Generate age keypair if not present ────────────────────────────────────
if [ -f "${AGE_KEY}" ]; then
    echo "[1/4] Age keypair already exists — skipping generation"
else
    echo "[1/4] Generating age keypair..."
    age-keygen -o "${AGE_KEY}" 2>/dev/null
    # Extract public key line from the key file
    grep "^# public key:" "${AGE_KEY}" | awk '{print $NF}' > "${AGE_PUB}"
    chmod 600 "${AGE_KEY}"
    chmod 644 "${AGE_PUB}"
    chown root:docker "${AGE_KEY}" "${AGE_PUB}"
    echo "  Private key: ${AGE_KEY} (chmod 600)"
    echo "  Public key:  ${AGE_PUB}"
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  CRITICAL: BACK UP THE PRIVATE KEY NOW                          ║"
    echo "║                                                                  ║"
    echo "║  ${AGE_KEY}                          ║"
    echo "║                                                                  ║"
    echo "║  Without this key, encrypted backups are unreadable.            ║"
    echo "║  Copy it to a password manager or offline device.               ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
fi

# ── 2. Configure rclone (interactive) ─────────────────────────────────────────
echo "[2/4] Checking rclone B2 configuration..."
if rclone listremotes | grep -q "b2:"; then
    echo "  rclone B2 remote already configured"
else
    echo "  No B2 remote found. Starting interactive rclone config..."
    echo "  Select 'b' for Backblaze B2, then enter your account ID and application key."
    echo "  Name the remote 'b2' when prompted."
    echo ""
    rclone config
fi

mkdir -p "${BACKUP_DIR}"
chown perfectday:docker "${BACKUP_DIR}"

# ── 3. Write backup script ────────────────────────────────────────────────────
echo "[3/4] Installing backup script..."
cat > /usr/local/bin/perfect-day-backup <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR='${BACKUP_DIR}'
AGE_PUB='${AGE_PUB}'
KEEP_DAYS='${KEEP_DAYS}'
DATE=\$(date +%F)
OUTFILE="\${BACKUP_DIR}/backup-\${DATE}.sql.gz.age"

source /etc/perfect-day/app.env

echo "[backup] Starting backup: \${DATE}"

# pg_dump → gzip → age encrypt
PGPASSWORD="\${POSTGRES_PASSWORD}" pg_dump \
    -h localhost -U perfectday perfectday \
    --no-password \
    | gzip \
    | age --recipients-file "\${AGE_PUB}" \
    > "\${OUTFILE}"

echo "[backup] Encrypted backup written: \${OUTFILE}"
echo "[backup] Size: \$(du -sh \${OUTFILE} | cut -f1)"

# Upload to B2
rclone sync "\${BACKUP_DIR}/" b2:perfect-day-backups/
echo "[backup] Uploaded to B2"

# Remove old local backups
find "\${BACKUP_DIR}" -name "backup-*.sql.gz.age" -mtime "+\${KEEP_DAYS}" -delete
echo "[backup] Pruned local backups older than \${KEEP_DAYS} days"

echo "[backup] Done"
SCRIPT

chmod 755 /usr/local/bin/perfect-day-backup

# ── 4. Install systemd service + timer ────────────────────────────────────────
echo "[4/4] Installing systemd timer..."

cat > /etc/systemd/system/perfect-day-backup.service <<EOF
[Unit]
Description=Perfect Day encrypted database backup
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/perfect-day-backup
StandardOutput=append:${LOG_DIR}/backup.log
StandardError=append:${LOG_DIR}/backup.log
User=root
EOF

cat > /etc/systemd/system/perfect-day-backup.timer <<EOF
[Unit]
Description=Perfect Day daily backup timer

[Timer]
OnCalendar=*-*-* ${TIMER_HOUR}:${TIMER_MIN}:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now perfect-day-backup.timer

echo ""
echo "Backup provisioning complete."
echo ""
echo "Timer schedule: daily at ${TIMER_HOUR}:$(printf '%02d' ${TIMER_MIN})"
echo ""
echo "Verify with:"
echo "  systemctl status perfect-day-backup.timer"
echo "  systemctl start perfect-day-backup.service   # manual test run"
echo "  ls ${BACKUP_DIR}/"
echo "  rclone ls b2:perfect-day-backups"
