#!/usr/bin/env bash
# gen-secrets.sh — generates cryptographically random secrets for .env
# Outputs shell export statements; redirect into a file or eval into your shell.
set -euo pipefail

hex32() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
hex64() { openssl rand -hex 32; }

echo "# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) — store securely, do not commit"
echo "SECRET_KEY=$(hex64)"
echo "MASTER_SECRET=$(hex32)"
echo "OAUTH_TOKEN_SECRET=$(hex32)"
echo "POSTGRES_PASSWORD=$(hex32)"
