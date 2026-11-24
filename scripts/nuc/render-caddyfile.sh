#!/usr/bin/env bash
# scripts/nuc/render-caddyfile.sh — Render Caddyfile from template
# Reads FORTIGATE_LAN_IP from the environment.
# - If set: validates as IPv4 dotted-quad, substitutes into Caddyfile.tmpl
# - If empty: substitutes Caddy's built-in 'private_ranges' keyword (RFC1918 fallback for local dev)
# Exits non-zero if FORTIGATE_LAN_IP is set but malformed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TMPL="${REPO_ROOT}/deploy/caddy/Caddyfile.tmpl"
OUT="${REPO_ROOT}/deploy/caddy/Caddyfile"

if [ ! -f "${TMPL}" ]; then
    echo "ERROR: Template not found: ${TMPL}" >&2
    exit 1
fi

IP="${FORTIGATE_LAN_IP:-}"

if [ -n "${IP}" ]; then
    # Validate IPv4 dotted-quad
    if ! echo "${IP}" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
        echo "ERROR: FORTIGATE_LAN_IP='${IP}' is not a valid IPv4 address." >&2
        exit 1
    fi
    sed "s|{{FORTIGATE_LAN_IP}}|${IP}|g" "${TMPL}" > "${OUT}"
    echo "  Caddyfile rendered: trusted_proxies static ${IP}"
    # Traffic from Cloudflare reaches the NUC from the FortiGate, the NUC's default gateway.
    # Trusting any other address makes every visitor look like one (tracker row 71).
    GW="${PD_DEFAULT_GATEWAY:-$(ip route show default 2>/dev/null | awk '{print $3; exit}')}"
    if [ -n "${GW}" ] && [ "${GW}" != "${IP}" ]; then
        echo "  WARNING: FORTIGATE_LAN_IP is ${IP}, but this machine's default gateway is ${GW}." >&2
        echo "           If the FortiGate is the gateway, set it with:" >&2
        echo "           sudo python3 scripts/nuc/lib/envfile.py set /etc/perfect-day/app.env FORTIGATE_LAN_IP ${GW}" >&2
    fi
else
    sed "s|{{FORTIGATE_LAN_IP}}|private_ranges|g" "${TMPL}" > "${OUT}"
    echo "  Caddyfile rendered: trusted_proxies static private_ranges (FORTIGATE_LAN_IP not set — dev fallback)"
fi

chmod 644 "${OUT}"
