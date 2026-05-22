#!/usr/bin/env bash
# wait-for-healthy.sh — polls a URL until it returns HTTP 200 or timeout
# Usage: ./scripts/wait-for-healthy.sh <url> [timeout_seconds]
set -euo pipefail

URL="${1:?Usage: $0 <url> [timeout_seconds]}"
TIMEOUT="${2:-60}"
INTERVAL=3
ELAPSED=0

echo "Waiting for ${URL} (timeout ${TIMEOUT}s)..."
until curl -sf --max-time 3 "${URL}" > /dev/null 2>&1; do
  if [ "${ELAPSED}" -ge "${TIMEOUT}" ]; then
    echo "ERROR: ${URL} did not become healthy within ${TIMEOUT}s" >&2
    exit 1
  fi
  sleep "${INTERVAL}"
  ELAPSED=$(( ELAPSED + INTERVAL ))
done

echo "✓ ${URL} is healthy (${ELAPSED}s)"
