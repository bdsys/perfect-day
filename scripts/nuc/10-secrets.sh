#!/usr/bin/env bash
# scripts/nuc/10-secrets.sh — Provision application secrets on the NUC
# Run on the NUC as: sudo ./scripts/nuc/10-secrets.sh
# Writes /etc/perfect-day/app.env (chmod 600, root:docker).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

ENV_FILE=/etc/perfect-day/app.env
LOG_DIR=/var/log/perfect-day

mkdir -p "${LOG_DIR}"

echo "=== Perfect Day Secrets Provisioning ==="
echo ""
echo "This script writes application secrets to ${ENV_FILE}."
echo "The file is readable only by root and the docker group."
echo ""

# ── Helper ────────────────────────────────────────────────────────────────────
prompt_secret() {
    local varname="$1"
    local prompt="$2"
    local value=""
    while [[ -z "${value}" ]]; do
        read -rsp "${prompt}: " value
        echo ""
        if [[ -z "${value}" ]]; then
            echo "  (Value cannot be empty. Press Ctrl+C to abort.)"
        fi
    done
    printf '%s' "${value}"
}

prompt_optional() {
    local varname="$1"
    local prompt="$2"
    local value=""
    read -rsp "${prompt} (leave blank to skip): " value
    echo ""
    printf '%s' "${value}"
}

hex32() {
    python3 -c "import secrets; print(secrets.token_hex(32))"
}

# ── Operator-provided secrets ─────────────────────────────────────────────────
echo "--- API Keys (operator-provided) ---"
echo ""

ANTHROPIC_API_KEY=$(prompt_secret ANTHROPIC_API_KEY "ANTHROPIC_API_KEY")
GOOGLE_CLIENT_ID=$(prompt_secret GOOGLE_CLIENT_ID "GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET=$(prompt_secret GOOGLE_CLIENT_SECRET "GOOGLE_CLIENT_SECRET")
SENDGRID_API_KEY=$(prompt_optional SENDGRID_API_KEY "SENDGRID_API_KEY (email notifications)")

echo ""
echo "--- Cloudflare DDNS (optional) ---"
echo "Skip these if FortiGate handles DDNS or you don't need DDNS yet."
echo "You will need: API token (Zone.DNS:Edit scope) and Zone ID from the CF dashboard."
echo ""
CLOUDFLARE_API_TOKEN=$(prompt_optional CLOUDFLARE_API_TOKEN "CLOUDFLARE_API_TOKEN")
CLOUDFLARE_ZONE_ID=$(prompt_optional CLOUDFLARE_ZONE_ID "CLOUDFLARE_ZONE_ID")

echo ""
echo "--- Generating cryptographic secrets ---"

# ── Auto-generated secrets ────────────────────────────────────────────────────
MASTER_SECRET=$(hex32)
echo "  MASTER_SECRET        generated"

OAUTH_TOKEN_SECRET=$(hex32)
echo "  OAUTH_TOKEN_SECRET   generated"

SECRET_KEY=$(hex32)
echo "  SECRET_KEY           generated"

POSTGRES_PASSWORD=$(hex32)
echo "  POSTGRES_PASSWORD    generated"

MINIO_ROOT_PASSWORD=$(openssl rand -hex 24)
echo "  MINIO_ROOT_PASSWORD  generated"

echo ""
echo "--- Writing ${ENV_FILE} ---"

# ── Write env file ─────────────────────────────────────────────────────────────
cat > "${ENV_FILE}" <<EOF
# Perfect Day — Production Environment
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Host: $(hostname)
#
# IMPORTANT: This file contains cryptographic keys.
# Back it up to a password manager or offline device immediately.
# If MASTER_SECRET or OAUTH_TOKEN_SECRET is lost, encrypted OAuth tokens
# in the database become permanently unreadable (users must re-authorize).

ENV=production

# Database (consumed by both Postgres container and API)
POSTGRES_USER=perfectday
POSTGRES_DB=perfectday
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=postgresql+asyncpg://perfectday:${POSTGRES_PASSWORD}@postgres:5432/perfectday
DATABASE_URL_SYNC=postgresql://perfectday:${POSTGRES_PASSWORD}@postgres:5432/perfectday

# Redis / Celery
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# MinIO container creds
MINIO_ROOT_USER=perfectday
MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}

# S3 (API client config — points at MinIO using the same creds)
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=perfectday
S3_SECRET_KEY=${MINIO_ROOT_PASSWORD}
S3_BUCKET_PHOTOS=photos
S3_REGION=us-east-1

# JWT / crypto
SECRET_KEY=${SECRET_KEY}
MASTER_SECRET=${MASTER_SECRET}
OAUTH_TOKEN_SECRET=${OAUTH_TOKEN_SECRET}

# CORS
CORS_ORIGINS=["https://diary.perfectday.andrewlass.com"]

# Google OAuth
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
GOOGLE_REDIRECT_URI=https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback

# Anthropic
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

# SendGrid (optional — email notifications)
SENDGRID_API_KEY=${SENDGRID_API_KEY:-}
EMAIL_FROM=pd@bdsys.net

# Rate limiting
RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_AUTH=10/minute

# Web (Next.js)
NEXT_PUBLIC_API_URL=https://api.diary.perfectday.andrewlass.com
EOF

chmod 600 "${ENV_FILE}"
chown root:docker "${ENV_FILE}"

echo "  Written: ${ENV_FILE}"
echo "  Mode:    $(stat -c '%a %U:%G' ${ENV_FILE})"

# ── Write Cloudflare DDNS config ──────────────────────────────────────────────
DDNS_CONFIG=/etc/perfect-day/cloudflare-ddns.config.json
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ZONE_ID:-}" ]]; then
    echo ""
    echo "--- Writing ${DDNS_CONFIG} ---"
    cat > "${DDNS_CONFIG}" <<EOF
{
  "cloudflare": [
    {
      "authentication": { "api_token": "${CLOUDFLARE_API_TOKEN}" },
      "zone_id": "${CLOUDFLARE_ZONE_ID}",
      "subdomains": [
        { "name": "diary.perfectday" },
        { "name": "api.diary.perfectday" },
        { "name": "media.diary.perfectday" }
      ],
      "proxied": false
    }
  ],
  "a": true,
  "aaaa": false,
  "purgeUnknownRecords": false,
  "ttl": 300
}
EOF
    chmod 600 "${DDNS_CONFIG}"
    chown root:docker "${DDNS_CONFIG}"
    echo "  Written: ${DDNS_CONFIG}"
    echo "  Mode:    $(stat -c '%a %U:%G' ${DDNS_CONFIG})"
else
    echo ""
    echo "  Cloudflare DDNS config skipped."
    echo "  Note: the cloudflare-ddns container will fail to start until"
    echo "  ${DDNS_CONFIG} exists. Re-run this script later when you"
    echo "  have your Cloudflare API token and Zone ID, or stop the service:"
    echo "    cd /opt/perfect-day && docker compose stop cloudflare-ddns"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  CRITICAL: BACK UP THIS FILE NOW                                ║"
echo "║                                                                  ║"
echo "║  Copy /etc/perfect-day/app.env to a password manager or an     ║"
echo "║  offline device before continuing.                               ║"
echo "║                                                                  ║"
echo "║  If this NUC's disk fails and you have no backup:               ║"
echo "║  - MASTER_SECRET and OAUTH_TOKEN_SECRET are unrecoverable.      ║"
echo "║  - All encrypted OAuth tokens in the database become            ║"
echo "║    permanently unreadable.                                       ║"
echo "║  - Users will need to re-authorize Google Calendar.             ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next step: run scripts/nuc/20-deploy.sh to deploy the application."
