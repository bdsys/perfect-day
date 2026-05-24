#!/usr/bin/env bash
# smoke-test.sh — curl-based API walkthrough asserting HTTP status codes at each step
# Usage: ./scripts/smoke-test.sh [api_base_url]
# Default: http://localhost:8000
set -euo pipefail

BASE="${1:-http://localhost:8000}"
PASS=0
FAIL=0

_check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "  ✓ ${desc} (HTTP ${actual})"
    PASS=$(( PASS + 1 ))
  else
    echo "  ✗ ${desc} — expected HTTP ${expected}, got HTTP ${actual}" >&2
    FAIL=$(( FAIL + 1 ))
  fi
}

_http() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

echo "=== Perfect Day Smoke Test against ${BASE} ==="

# ---- Health ----
echo ""
echo "--- Health ---"
_check "GET /healthz" 200 "$(_http "${BASE}/healthz")"
_check "GET /readyz"  200 "$(_http "${BASE}/readyz")"

# ---- Auth: register ----
echo ""
echo "--- Auth ---"
TIMESTAMP=$(date +%s)
EMAIL="smoke+${TIMESTAMP}@example.com"
PASSWORD="Password1!"

REGISTER=$(curl -sf -X POST "${BASE}/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")
_check "POST /v1/auth/register" 201 \
  "$(_http -X POST "${BASE}/v1/auth/register" \
     -H "Content-Type: application/json" \
     -d "{\"email\":\"smoke2+${TIMESTAMP}@example.com\",\"password\":\"${PASSWORD}\"}")"

TOKEN=$(echo "${REGISTER}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

_check "POST /v1/auth/login" 200 \
  "$(_http -X POST "${BASE}/v1/auth/login" \
     -H "Content-Type: application/json" \
     -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")"

_check "GET /v1/auth/me" 200 \
  "$(_http "${BASE}/v1/auth/me" -H "Authorization: Bearer ${TOKEN}")"

# ---- Diaries ----
echo ""
echo "--- Diaries ---"
DIARY_BODY=$(mktemp)
DIARY_STATUS=$(curl -s -o "${DIARY_BODY}" -w "%{http_code}" -X POST "${BASE}/v1/diaries" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Smoke Test Diary\",\"timezone\":\"UTC\"}")
_check "POST /v1/diaries" 201 "${DIARY_STATUS}"
DIARY_ID=$(python3 -c "import json; print(json.load(open('${DIARY_BODY}'))['id'])")

_check "GET /v1/diaries" 200 \
  "$(_http "${BASE}/v1/diaries" -H "Authorization: Bearer ${TOKEN}")"

_check "GET /v1/diaries/{id}" 200 \
  "$(_http "${BASE}/v1/diaries/${DIARY_ID}" -H "Authorization: Bearer ${TOKEN}")"

# ---- Entries ----
echo ""
echo "--- Entries ---"
TODAY=$(date +%Y-%m-%d)
ENTRY=$(curl -sf -X POST "${BASE}/v1/diaries/${DIARY_ID}/entries" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"entry_date\":\"${TODAY}\",\"title\":\"Smoke Entry\",\"body_markdown\":\"Hello world.\"}")
ENTRY_ID=$(echo "${ENTRY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

_check "POST /v1/diaries/{id}/entries" 201 \
  "$(_http -X POST "${BASE}/v1/diaries/${DIARY_ID}/entries" \
     -H "Authorization: Bearer ${TOKEN}" \
     -H "Content-Type: application/json" \
     -d "{\"entry_date\":\"${TODAY}\",\"title\":\"Entry2\"}")"

_check "GET /v1/diaries/{id}/entries" 200 \
  "$(_http "${BASE}/v1/diaries/${DIARY_ID}/entries" -H "Authorization: Bearer ${TOKEN}")"

_check "GET /v1/entries/{id}" 200 \
  "$(_http "${BASE}/v1/entries/${ENTRY_ID}" -H "Authorization: Bearer ${TOKEN}")"

_check "PATCH /v1/entries/{id}" 200 \
  "$(_http -X PATCH "${BASE}/v1/entries/${ENTRY_ID}" \
     -H "Authorization: Bearer ${TOKEN}" \
     -H "Content-Type: application/json" \
     -d "{\"body_markdown\":\"Updated body.\"}")"

_check "POST /v1/entries/{id}/publish" 200 \
  "$(_http -X POST "${BASE}/v1/entries/${ENTRY_ID}/publish" \
     -H "Authorization: Bearer ${TOKEN}")"

_check "POST /v1/entries/{id}/unpublish" 200 \
  "$(_http -X POST "${BASE}/v1/entries/${ENTRY_ID}/unpublish" \
     -H "Authorization: Bearer ${TOKEN}")"

# ---- Scan endpoint (queues task; 202/409 both acceptable) ----
echo ""
echo "--- Scan ---"
SCAN_STATUS=$(_http -X POST "${BASE}/v1/diaries/${DIARY_ID}/scan/run" \
  -H "Authorization: Bearer ${TOKEN}")
if [ "${SCAN_STATUS}" = "202" ] || [ "${SCAN_STATUS}" = "409" ]; then
  echo "  ✓ POST /v1/diaries/{id}/scan/run (HTTP ${SCAN_STATUS})"
  PASS=$(( PASS + 1 ))
else
  echo "  ✗ POST /v1/diaries/{id}/scan/run — expected 202 or 409, got ${SCAN_STATUS}" >&2
  FAIL=$(( FAIL + 1 ))
fi

# ---- Integrations ----
echo ""
echo "--- Integrations ---"
_check "GET /v1/integrations" 200 \
  "$(_http "${BASE}/v1/integrations" -H "Authorization: Bearer ${TOKEN}")"

_check "GET /v1/integrations/google/authorize" 200 \
  "$(_http "${BASE}/v1/integrations/google/authorize?scopes=calendar" \
     -H "Authorization: Bearer ${TOKEN}")"

# ---- Summary ----
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[ "${FAIL}" -eq 0 ] || exit 1
