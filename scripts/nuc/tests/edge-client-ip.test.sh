#!/usr/bin/env bash
# Tests that the Caddy edge hands the API the visitor's real address (tracker row 71).
# Renders the real Caddyfile.tmpl, runs it in Docker in front of a stand-in "api" and
# "web" that echo the X-Forwarded-For they receive, and checks what arrives.
# Needs Docker (pulls caddy:2-alpine); skips itself without it. Runs in `make test-scripts`.
# Usage: bash scripts/nuc/tests/edge-client-ip.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
WORK="$(mktemp -d)"
NET="pd-edge-test-$$"
IMAGE="caddy:2-alpine"
PORT=18080
FAILS=0

if ! docker info >/dev/null 2>&1; then
    echo "  skip edge client-IP tests: Docker is not available"
    exit 0
fi

cleanup() {
    docker rm -f "${NET}-edge" "${NET}-up" >/dev/null 2>&1 || true
    docker network rm "${NET}" >/dev/null 2>&1 || true
    rm -rf "${WORK}"
}
trap cleanup EXIT

check() { # check <description> <expected> <actual>
    if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1 (expected '$2', got '$3')"; FAILS=$((FAILS + 1)); fi
}
check_not() { # check_not <description> <unwanted> <actual>
    if [ "$2" != "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1 (got '$3')"; FAILS=$((FAILS + 1)); fi
}
check_absent() { # check_absent <description> <unwanted substring> <actual>
    case "$3" in *"$2"*) echo "  FAIL $1 (got '$3')"; FAILS=$((FAILS + 1)) ;; *) echo "  ok   $1" ;; esac
}

# Stand-in upstream: answers on both backend ports with the X-Forwarded-For it was sent.
cat > "${WORK}/upstream.Caddyfile" <<'EOF'
{
	admin off
	auto_https off
}
:8000, :3000 {
	respond "{header.X-Forwarded-For}"
}
EOF

docker network create "${NET}" >/dev/null
docker run -d --name "${NET}-up" --network "${NET}" --network-alias api --network-alias web \
    -v "${WORK}/upstream.Caddyfile:/etc/caddy/Caddyfile:ro" "${IMAGE}" >/dev/null

# Render the real template the way the deploy does, into a scratch copy.
render() { # render <FORTIGATE_LAN_IP value>
    cp "${REPO}/deploy/caddy/Caddyfile.tmpl" "${WORK}/Caddyfile.tmpl"
    if [ -n "$1" ]; then
        sed "s|{{FORTIGATE_LAN_IP}}|$1|g" "${WORK}/Caddyfile.tmpl" > "${WORK}/Caddyfile"
    else
        sed "s|{{FORTIGATE_LAN_IP}}|private_ranges|g" "${WORK}/Caddyfile.tmpl" > "${WORK}/Caddyfile"
    fi
    # The test must exercise what render-caddyfile.sh produces: same substitution.
    FORTIGATE_LAN_IP="$1" bash "${REPO}/scripts/nuc/render-caddyfile.sh" >/dev/null
    cmp -s "${REPO}/deploy/caddy/Caddyfile" "${WORK}/Caddyfile" || { echo "  FAIL render differs"; FAILS=$((FAILS + 1)); }
}

start_edge() {
    docker rm -f "${NET}-edge" >/dev/null 2>&1 || true
    docker run -d --name "${NET}-edge" --network "${NET}" -p "127.0.0.1:${PORT}:80" \
        -v "${WORK}/Caddyfile:/etc/caddy/Caddyfile:ro" "${IMAGE}" >/dev/null
    for _ in $(seq 1 50); do
        curl -s -o /dev/null "http://127.0.0.1:${PORT}/" && return 0
        sleep 0.2
    done
    echo "  FAIL edge did not start"; docker logs "${NET}-edge" | tail -5; exit 1
}

ask() { # ask <host> [curl header args...]: what the backend saw as X-Forwarded-For
    local host="$1"; shift
    curl -s --max-time 5 -H "Host: ${host}" "$@" "http://127.0.0.1:${PORT}/"
}

API=perfectday-api.andrewlass.com
WEB=perfectday.andrewlass.com

echo "Trusted hop (dev fallback private_ranges: the test's own requests come from Docker's gateway):"
render ""
start_edge
check "API gets the visitor from CF-Connecting-IP" "203.0.113.7" "$(ask "${API}" -H 'CF-Connecting-IP: 203.0.113.7')"
check "web gets it too" "203.0.113.7" "$(ask "${WEB}" -H 'CF-Connecting-IP: 203.0.113.7')"
check "a forged X-Forwarded-For is replaced, not passed on" "203.0.113.7" \
    "$(ask "${API}" -H 'CF-Connecting-IP: 203.0.113.7' -H 'X-Forwarded-For: 198.51.100.9')"
GW="$(ask "${API}")"
check_not "without CF-Connecting-IP the backend gets the connecting address" "" "${GW}"
check_absent "  ...and never a forged X-Forwarded-For" "198.51.100.9" "$(ask "${API}" -H 'X-Forwarded-For: 198.51.100.9')"

echo "Untrusted hop (FORTIGATE_LAN_IP names some other device):"
render "192.0.2.1"
start_edge
check "CF-Connecting-IP from an untrusted hop is ignored" "${GW}" "$(ask "${API}" -H 'CF-Connecting-IP: 203.0.113.7')"
check "  ...and so is a forged X-Forwarded-For" "${GW}" "$(ask "${API}" -H 'X-Forwarded-For: 198.51.100.9')"

echo "The deploy warns when FORTIGATE_LAN_IP is not the NUC's default gateway:"
OUT="$(FORTIGATE_LAN_IP=192.0.2.1 PD_DEFAULT_GATEWAY=192.0.2.254 bash "${REPO}/scripts/nuc/render-caddyfile.sh" 2>&1)"
check "a mismatch prints a warning" "yes" "$(case "${OUT}" in *WARNING*192.0.2.254*) echo yes ;; *) echo no ;; esac)"
OUT="$(FORTIGATE_LAN_IP=192.0.2.254 PD_DEFAULT_GATEWAY=192.0.2.254 bash "${REPO}/scripts/nuc/render-caddyfile.sh" 2>&1)"
check_absent "a match prints none" "WARNING" "${OUT}"
OUT="$(FORTIGATE_LAN_IP= PD_DEFAULT_GATEWAY=192.0.2.254 bash "${REPO}/scripts/nuc/render-caddyfile.sh" 2>&1)"
check_absent "the dev fallback prints none" "WARNING" "${OUT}"

echo "Public link tokens stay out of the access log:"
ask_path() { # ask_path <host> <path>
    curl -s --max-time 5 -o /dev/null -H "Host: $1" "http://127.0.0.1:${PORT}$2"
}
ask_path "${WEB}" "/p/SECRETTOKEN?x=1"
ask_path "${API}" "/v1/public/SECRETTOKEN/claims"
sleep 0.5
EDGE_LOG="$(docker logs "${NET}-edge" 2>&1)"
check_absent "the access log never holds a public token" "SECRETTOKEN" "${EDGE_LOG}"
check "the log still holds /p/[redacted]" "yes" "$(case "${EDGE_LOG}" in *'/p/[redacted]'*) echo yes ;; *) echo no ;; esac)"
check "and /v1/public/[redacted]" "yes" "$(case "${EDGE_LOG}" in *'/v1/public/[redacted]'*) echo yes ;; *) echo no ;; esac)"

echo
if [ "${FAILS}" -eq 0 ]; then echo "All edge client-IP tests passed."; else echo "${FAILS} failed."; exit 1; fi
