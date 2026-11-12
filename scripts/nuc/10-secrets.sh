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

# ── Helpers ───────────────────────────────────────────────────────────────────
# These are called as VAR=$(helper ...), so stdout must carry the value and
# nothing else. Prompts and the newline after a hidden read go to stderr;
# otherwise every captured value starts with "\n" (GitHub issue #39).

# Print the current value of KEY from an existing env file, or nothing.
existing_value() {
    local key="$1"
    [[ -f "${ENV_FILE}" ]] || return 0
    sed -n "s/^${key}=//p" "${ENV_FILE}" | head -n 1
}

# Required value. If one is already stored, a blank answer keeps it.
prompt_secret() {
    local varname="$1"
    local prompt="$2"
    local current value=""
    current="$(existing_value "${varname}")"
    [[ -n "${current}" ]] && prompt="${prompt} (blank keeps current value)"
    while [[ -z "${value}" ]]; do
        read -rsp "${prompt}: " value || true
        echo "" >&2
        if [[ -z "${value}" && -n "${current}" ]]; then
            value="${current}"
        elif [[ -z "${value}" ]]; then
            echo "  (Value cannot be empty. Press Ctrl+C to abort.)" >&2
        fi
    done
    printf '%s' "${value}"
}

# Optional value. A blank answer keeps the stored value, or skips if none.
prompt_optional() {
    local varname="$1"
    local prompt="$2"
    local current value=""
    current="$(existing_value "${varname}")"
    if [[ -n "${current}" ]]; then
        prompt="${prompt} (blank keeps current value)"
    else
        prompt="${prompt} (leave blank to skip)"
    fi
    read -rsp "${prompt}: " value || true
    echo "" >&2
    printf '%s' "${value:-${current}}"
}

hex32() {
    python3 -c "import secrets; print(secrets.token_hex(32))"
}

# Generated secrets are created once and then kept for the life of the install.
# Photos and OAuth tokens are encrypted under MASTER_SECRET / OAUTH_TOKEN_SECRET
# and the Postgres and MinIO volumes are initialised with their passwords, so
# regenerating any of them on a re-run would make existing data unreadable.
keep_or_generate() {
    local varname="$1"
    local generator="$2"
    local current
    current="$(existing_value "${varname}")"
    if [[ -n "${current}" ]]; then
        echo "  ${varname} kept (already provisioned)" >&2
        printf '%s' "${current}"
    else
        echo "  ${varname} generated" >&2
        "${generator}"
    fi
}

hex24() {
    openssl rand -hex 24
}
# ── End helpers ───────────────────────────────────────────────────────────────

if [[ -f "${ENV_FILE}" ]]; then
    BACKUP_FILE="${ENV_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p "${ENV_FILE}" "${BACKUP_FILE}"
    echo "Existing ${ENV_FILE} found."
    echo "  Backed up to ${BACKUP_FILE}"
    echo "  Cryptographic secrets and passwords will be kept, not regenerated."
    echo ""
fi

# ── Operator-provided secrets ─────────────────────────────────────────────────
echo "--- API Keys (operator-provided) ---"
echo ""

ANTHROPIC_API_KEY=$(prompt_secret ANTHROPIC_API_KEY "ANTHROPIC_API_KEY")
GEMINI_API_KEY=$(prompt_optional GEMINI_API_KEY "GEMINI_API_KEY (LLM fallback)")
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
echo "--- Caddy edge (FortiGate LAN IP) ---"
echo "The FortiGate LAN IP is used by Caddy to scope trusted upstream proxies."
echo "Example: 192.168.1.1 (the IP FortiGate uses on your home LAN)."
echo "Leave blank for local dev — Caddy will trust all RFC1918 ranges instead."
echo ""
FORTIGATE_LAN_IP=$(prompt_optional FORTIGATE_LAN_IP "FORTIGATE_LAN_IP (FortiGate LAN IP, e.g. 192.168.1.1)")

echo ""
echo "--- Generating cryptographic secrets ---"

# ── Auto-generated secrets (kept if already provisioned) ─────────────────────
MASTER_SECRET=$(keep_or_generate MASTER_SECRET hex32)
OAUTH_TOKEN_SECRET=$(keep_or_generate OAUTH_TOKEN_SECRET hex32)
SECRET_KEY=$(keep_or_generate SECRET_KEY hex32)
POSTGRES_PASSWORD=$(keep_or_generate POSTGRES_PASSWORD hex32)
MINIO_ROOT_PASSWORD=$(keep_or_generate MINIO_ROOT_PASSWORD hex24)

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
# If MASTER_SECRET is lost, every stored photo is permanently unreadable.
# If OAUTH_TOKEN_SECRET is lost, stored OAuth tokens are unreadable
# (users must re-authorize).

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

# Gemini (optional — used as LLM fallback when Anthropic fails)
GEMINI_API_KEY=${GEMINI_API_KEY:-}

# SendGrid (optional — email notifications)
SENDGRID_API_KEY=${SENDGRID_API_KEY:-}
EMAIL_FROM=pd@bdsys.net

# Rate limiting
RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_AUTH=10/minute

# Caddy edge
FORTIGATE_LAN_IP=${FORTIGATE_LAN_IP:-}

# Web (Next.js)
NEXT_PUBLIC_API_URL=https://api.diary.perfectday.andrewlass.com
NEXT_PUBLIC_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
EOF

# 640, not 600: compose runs as the perfectday service user (docker group) and
# has to read this file through the .env symlinks.
chown root:docker "${ENV_FILE}"
chmod 640 "${ENV_FILE}"

echo "  Written: ${ENV_FILE}"
echo "  Mode:    $(stat -c '%a %U:%G' ${ENV_FILE})"

# ── Write Cloudflare DDNS config ──────────────────────────────────────────────
DDNS_CONFIG=/etc/perfect-day/cloudflare-ddns.config.json
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ZONE_ID:-}" ]]; then
    echo ""
    echo "--- Writing ${DDNS_CONFIG} ---"
    # jq escapes the values, so a stray character can never produce invalid JSON.
    jq -n --arg token "${CLOUDFLARE_API_TOKEN}" --arg zone "${CLOUDFLARE_ZONE_ID}" '{
      cloudflare: [
        {
          authentication: { api_token: $token },
          zone_id: $zone,
          subdomains: ["diary.perfectday", "api.diary.perfectday", "media.diary.perfectday"],
          proxied: false
        }
      ],
      a: true,
      aaaa: false,
      purgeUnknownRecords: false,
      ttl: 300
    }' > "${DDNS_CONFIG}"
    chmod 600 "${DDNS_CONFIG}"
    chown root:docker "${DDNS_CONFIG}"
    echo "  Written: ${DDNS_CONFIG}"
    echo "  Mode:    $(stat -c '%a %U:%G' ${DDNS_CONFIG})"
else
    echo ""
    echo "  Cloudflare DDNS config skipped."
    echo "  Note: the cloudflare-ddns container will fail to start until"
    echo "  ${DDNS_CONFIG} exists. Re-run this script later when you"
    echo "  have your Cloudflare API token and Zone ID (existing secrets are"
    echo "  kept on a re-run), or stop the service:"
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
echo "║  - Every stored photo becomes permanently unreadable.           ║"
echo "║  - All encrypted OAuth tokens in the database become            ║"
echo "║    permanently unreadable; users must re-authorize Google.      ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next step: run scripts/nuc/20-deploy.sh to deploy the application."
