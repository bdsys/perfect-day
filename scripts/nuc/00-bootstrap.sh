#!/usr/bin/env bash
# scripts/nuc/00-bootstrap.sh — First-time NUC OS prep
# Run on the NUC as: sudo ./scripts/nuc/00-bootstrap.sh
# Idempotent: safe to re-run.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

LOG_DIR=/var/log/perfect-day
LOG_FILE="${LOG_DIR}/bootstrap-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Perfect Day NUC Bootstrap ==="
echo "Host: $(hostname)"
echo "Date: $(date)"
echo ""

# ── 1. System update ──────────────────────────────────────────────────────────
echo "[1/9] Updating apt..."
apt-get update -y
apt-get upgrade -y

# ── 2. Install required packages ─────────────────────────────────────────────
echo "[2/9] Installing packages..."
apt-get install -y \
    docker.io \
    docker-compose-plugin \
    ufw \
    unattended-upgrades \
    fail2ban \
    rclone \
    age \
    openssl \
    curl \
    jq \
    git \
    lsof

systemctl enable --now docker

# ── 3. Create service user ────────────────────────────────────────────────────
echo "[3/9] Creating perfectday user..."
if ! id perfectday &>/dev/null; then
    useradd --system --create-home --shell /bin/bash --groups docker perfectday
    echo "  Created user: perfectday"
else
    # Ensure user is in docker group even if it existed before
    usermod -aG docker perfectday
    echo "  User already exists; ensured docker group membership"
fi

if id andrew &>/dev/null; then
    usermod -aG docker andrew
    echo "  Added andrew to docker group (re-login required for it to take effect)"
fi

# ── 4. Create directory structure ─────────────────────────────────────────────
echo "[4/9] Creating directories..."
mkdir -p /opt/perfect-day
mkdir -p /var/log/perfect-day
mkdir -p /var/backups/perfect-day
chown -R perfectday:docker /opt/perfect-day
chown -R perfectday:docker /var/log/perfect-day
chown -R perfectday:docker /var/backups/perfect-day

# Secrets dir: root:docker, only docker group can read
mkdir -p /etc/perfect-day
chmod 750 /etc/perfect-day
chown root:docker /etc/perfect-day

# ── 5. UFW firewall ────────────────────────────────────────────────────────────
echo "[5/9] Configuring UFW..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"
# Postgres (5432), Redis (6379), MinIO (9000/9001) are NOT opened — internal only
ufw --force enable
echo "UFW status:"
ufw status verbose

# ── 6. fail2ban ────────────────────────────────────────────────────────────────
echo "[6/9] Configuring fail2ban..."
systemctl enable --now fail2ban
# The default sshd jail is active after install; no extra config needed

# ── 7. Unattended upgrades ────────────────────────────────────────────────────
echo "[7/9] Enabling unattended-upgrades..."
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades

# ── 8. Docker compose auto-start via systemd ──────────────────────────────────
echo "[8/9] Installing compose autostart service..."
cat > /etc/systemd/system/perfect-day.service <<'EOF'
[Unit]
Description=Perfect Day application stack
After=docker.service network-online.target
Wants=docker.service network-online.target
Requires=docker.service

[Service]
Type=forking
RemainAfterExit=yes
WorkingDirectory=/opt/perfect-day
ExecStart=/usr/bin/docker compose --profile nuc up -d
ExecStop=/usr/bin/docker compose --profile nuc down
Restart=on-failure
RestartSec=30
User=perfectday

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable perfect-day.service
echo "  Compose autostart service enabled (will activate after first deploy)"

# ── 9. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "[9/9] Bootstrap complete."
echo ""
echo "Next step: run scripts/nuc/10-secrets.sh to provision application secrets."
echo "Log file: ${LOG_FILE}"
