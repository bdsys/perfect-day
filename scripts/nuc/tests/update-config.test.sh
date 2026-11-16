#!/usr/bin/env bash
# Tests for scripts/nuc/15-update-config.sh. Runs on any machine with bash, python3 and jq;
# never touches /etc. Not wired into CI yet.
# Usage: bash scripts/nuc/tests/update-config.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../15-update-config.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}
val() { grep -E "^$2=" "$1" | tail -1 | cut -d= -f2-; }

setup() {
    rm -rf "${WORK:?}"/*
    cat > "${WORK}/app.env" <<'EOF'
ENV=production
MASTER_SECRET=keep-me
CORS_ORIGINS=["https://diary.perfectday.andrewlass.com"]
GOOGLE_CLIENT_ID=old-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=old-secret
GOOGLE_REDIRECT_URI=https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback
NEXT_PUBLIC_API_URL=https://api.diary.perfectday.andrewlass.com
EOF
    chmod 600 "${WORK}/app.env"
    # The broken file seen on the NUC: a newline pasted inside the token string.
    printf '{\n  "cloudflare": [\n    {\n      "authentication": { "api_token": "tok-123\n" },\n      "zone_id": "zone-abc",\n      "subdomains": ["diary.perfectday"],\n      "proxied": false\n    }\n  ],\n  "a": true\n}\n' > "${WORK}/ddns.json"
    chmod 600 "${WORK}/ddns.json"
    cat > "${WORK}/nuc-client.json" <<'EOF'
{"web":{"client_id":"new-id.apps.googleusercontent.com","client_secret":"new-secret",
"javascript_origins":["https://perfectday.andrewlass.com"],
"redirect_uris":["https://perfectday-api.andrewlass.com/v1/integrations/google/callback"]}}
EOF
    cat > "${WORK}/local-client.json" <<'EOF'
{"web":{"client_id":"local-id.apps.googleusercontent.com","client_secret":"local-secret",
"javascript_origins":["http://localhost"],
"redirect_uris":["http://localhost:8000/v1/integrations/google/callback"]}}
EOF
}

run() { PD_APP_ENV="${WORK}/app.env" PD_DDNS_CONFIG="${WORK}/ddns.json" PD_ALLOW_NONROOT=1 bash "${SCRIPT}" "$@"; }

echo "hostnames and Google client"
setup
OUT="$(run --google-client "${WORK}/nuc-client.json")"
E="${WORK}/app.env"
check "CORS is the web origin"        test "$(val "$E" CORS_ORIGINS)" = '["https://perfectday.andrewlass.com"]'
check "redirect URI is the API host"  test "$(val "$E" GOOGLE_REDIRECT_URI)" = "https://perfectday-api.andrewlass.com/v1/integrations/google/callback"
check "web API URL is the API host"   test "$(val "$E" NEXT_PUBLIC_API_URL)" = "https://perfectday-api.andrewlass.com"
check "client ID from the file"       test "$(val "$E" GOOGLE_CLIENT_ID)" = "new-id.apps.googleusercontent.com"
check "secret from the file"          test "$(val "$E" GOOGLE_CLIENT_SECRET)" = "new-secret"
check "web client ID added"           test "$(val "$E" NEXT_PUBLIC_GOOGLE_CLIENT_ID)" = "new-id.apps.googleusercontent.com"
check "other keys untouched"          test "$(val "$E" MASTER_SECRET)" = "keep-me"
check "mode stays 600"                test "$(stat -c %a "$E")" = "600"
check "backup kept"                   bash -c "ls '${WORK}'/app.env.bak-* >/dev/null"
check "backup has the old values"     bash -c "grep -q '^GOOGLE_CLIENT_SECRET=old-secret' '${WORK}'/app.env.bak-*"
check "secret never printed"          bash -c "! grep -q 'new-secret' <<< \"\$1\"" _ "${OUT}"

echo "DDNS config repaired"
D="${WORK}/ddns.json"
check "valid JSON now"                bash -c "jq -e . \"$D\" >/dev/null"
check "token kept, stray newline gone" test "$(jq -r '.cloudflare[0].authentication.api_token' "$D")" = "tok-123"
check "zone kept"                     test "$(jq -r '.cloudflare[0].zone_id' "$D")" = "zone-abc"
check "new subdomains"                test "$(jq -c '.cloudflare[0].subdomains' "$D")" = '["perfectday","perfectday-api","perfectday-media"]'
check "proxied true"                  test "$(jq '.cloudflare[0].proxied' "$D")" = "true"
check "mode stays 600"                test "$(stat -c %a "$D")" = "600"
check "token never printed"           bash -c "! grep -q 'tok-123' <<< \"\$1\"" _ "${OUT}"

echo "refuses the wrong client file"
setup
if run --google-client "${WORK}/local-client.json" >/dev/null 2>&1; then RC=0; else RC=1; fi
check "exits non-zero"                test "$RC" = 1
check "app.env unchanged"             test "$(val "${WORK}/app.env" GOOGLE_CLIENT_ID)" = "old-id.apps.googleusercontent.com"

echo "without a client file, only hostnames change"
setup
run >/dev/null
check "client ID untouched"           test "$(val "${WORK}/app.env" GOOGLE_CLIENT_ID)" = "old-id.apps.googleusercontent.com"
check "redirect URI updated"          test "$(val "${WORK}/app.env" GOOGLE_REDIRECT_URI)" = "https://perfectday-api.andrewlass.com/v1/integrations/google/callback"

echo "a DDNS file with no token is reported, not silently kept"
setup
printf '{"cloudflare":[{"zone_id":"zone-abc"}]}\n' > "${WORK}/ddns.json"
if OUT2="$(run 2>&1)"; then RC=0; else RC=$?; fi
check "exits non-zero"                test "$RC" != 0
check "says what is wrong"            bash -c "grep -q 'no API token' <<< \"\$1\"" _ "${OUT2}"
check "app.env still updated"         test "$(val "${WORK}/app.env" NEXT_PUBLIC_API_URL)" = "https://perfectday-api.andrewlass.com"

echo "--google-client without a file"
setup
if OUT3="$(run --google-client 2>&1)"; then RC=0; else RC=$?; fi
check "exits non-zero"                test "$RC" != 0
check "prints usage"                  bash -c "grep -q 'Usage' <<< \"\$1\"" _ "${OUT3}"

echo
if [ "${FAILS}" -eq 0 ]; then echo "all passed"; else echo "${FAILS} failed"; exit 1; fi
