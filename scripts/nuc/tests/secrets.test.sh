#!/usr/bin/env bash
# Tests for scripts/nuc/10-secrets.sh: a re-run keeps the storage address and the
# Garage RPC secret (tracker row 41). Writes only into a temp folder.
# Usage: bash scripts/nuc/tests/secrets.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="${HERE}/../10-secrets.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

export PD_SECRETS_ALLOW_NONROOT=1 PD_ETC="${TOP}/etc" PD_LOG_DIR="${TOP}/log"
mkdir -p "${PD_ETC}"
ENV_FILE="${PD_ETC}/app.env"
val() { sed -n "s/^$1=//p" "${ENV_FILE}"; }
# Prompts, in order: Anthropic, Gemini, Google id, Google secret, SendGrid,
# Cloudflare token, zone, FortiGate IP. Blank keeps the stored value.
FRESH_ANSWERS=$'anthropic-key\n\ngoogle-id\ngoogle-secret\n\n\n\n\n'
KEEP_ANSWERS=$'\n\n\n\n\n\n\n\n'

echo "fresh_install"
RC=0; OUT="$(bash "${SECRETS}" <<<"${FRESH_ANSWERS}" 2>&1)" || RC=$?
check "exits 0" test "${RC}" -eq 0
check "storage address is Garage" test "$(val S3_ENDPOINT_URL)" = "http://garage:3900"
check "Garage RPC secret is 64 hex" bash -c '[[ "$1" =~ ^[0-9a-f]{64}$ ]]' _ "$(val GARAGE_RPC_SECRET)"
check "S3 secret long enough for Garage (>=16)" test "$(val S3_SECRET_KEY | wc -c)" -gt 16
check "RPC secret never printed" bash -c '! grep -q "$1" <<<"$2"' _ "$(val GARAGE_RPC_SECRET)" "${OUT}"
check "one S3_ENDPOINT_URL line" test "$(grep -c '^S3_ENDPOINT_URL=' "${ENV_FILE}")" -eq 1

echo "rerun_keeps_minio_address_and_rpc_secret"
# An install from before the switch: its storage is still MinIO.
python3 "${HERE}/../lib/envfile.py" set "${ENV_FILE}" S3_ENDPOINT_URL http://minio:9000 >/dev/null
RPC="$(val GARAGE_RPC_SECRET)"; MASTER="$(val MASTER_SECRET)"
RC=0; OUT="$(bash "${SECRETS}" <<<"${KEEP_ANSWERS}" 2>&1)" || RC=$?
check "exits 0" test "${RC}" -eq 0
check "storage address kept" test "$(val S3_ENDPOINT_URL)" = "http://minio:9000"
check "RPC secret kept" test "$(val GARAGE_RPC_SECRET)" = "${RPC}"
check "MASTER_SECRET kept" test "$(val MASTER_SECRET)" = "${MASTER}"
check "says the address was kept" grep -q "S3_ENDPOINT_URL kept" <<<"${OUT}"

echo "rerun_on_old_install_without_rpc_secret"
grep -v '^GARAGE_RPC_SECRET=' "${ENV_FILE}" > "${TOP}/x" && cat "${TOP}/x" > "${ENV_FILE}"
RC=0; bash "${SECRETS}" <<<"${KEEP_ANSWERS}" >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "RPC secret generated" bash -c '[[ "$1" =~ ^[0-9a-f]{64}$ ]]' _ "$(val GARAGE_RPC_SECRET)"
check "address still kept" test "$(val S3_ENDPOINT_URL)" = "http://minio:9000"

echo "deploy_ensures_rpc_secret_before_restart"
# 40-update.sh cannot run here (root, git, /opt). Pin the order instead: a deploy
# must add a missing GARAGE_RPC_SECRET before it restarts anything (plan Review
# Focus 1), so Garage can always start.
UPDATE="${HERE}/../40-update.sh"
ensure_line="$({ grep -n 'envfile.py" ensure "${ENV_FILE}" GARAGE_RPC_SECRET hex32' "${UPDATE}" || true; } | head -n 1 | cut -d: -f1)"
restart_line="$({ grep -n "\[6/8\]" "${UPDATE}" || true; } | head -n 1 | cut -d: -f1)"
check "40-update.sh ensures GARAGE_RPC_SECRET" test -n "${ensure_line}"
check "before it restarts the app" test "${ensure_line:-999999}" -lt "${restart_line:-0}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "secrets: all passed"; else echo "secrets: ${FAILS} failed"; exit 1; fi
