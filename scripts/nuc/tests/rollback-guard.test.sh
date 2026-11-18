#!/usr/bin/env bash
# Tests for compose_serves_store (scripts/nuc/lib/backup.sh) and its use in
# 50-rollback.sh: after the switch to Garage, rolling back to a release whose compose
# file pins MinIO would break every photo while /readyz still said 200 (review,
# Important 1). Temp files only. Usage: bash scripts/nuc/tests/rollback-guard.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
# shellcheck source=../lib/backup.sh
source "${HERE}/../lib/backup.sh"

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}
refuses() { ! compose_serves_store "$@" 2>/dev/null; }

# The compose file before row 41: no garage, MinIO pinned in the app services.
cat > "${WORK}/old.yml" <<'YML'
services:
  postgres:
    image: postgres:16-alpine
  minio:
    image: minio/minio:latest
  api:
    environment:
      S3_ENDPOINT_URL: http://minio:9000
      S3_PUBLIC_ENDPOINT_URL: http://localhost:9000
YML
# Row 41 release 1: both stores, the address from config.
cat > "${WORK}/new.yml" <<'YML'
x-storage-env: &storage-env
  S3_ENDPOINT_URL: ${S3_ENDPOINT_URL:-http://garage:3900}
services:
  minio:
    profiles: ["minio"]
  garage:
    image: dxflrs/garage:v2.5.0
  api:
    environment:
      <<: *storage-env
YML
printf 'S3_ENDPOINT_URL=http://garage:3900\n' > "${WORK}/garage.env"
printf 'S3_ENDPOINT_URL=http://minio:9000\n' > "${WORK}/minio.env"

echo "after_the_switch"
check "old release refused (it pins minio, no garage)" refuses "${WORK}/old.yml" "${WORK}/garage.env"
check "release 1 accepted" compose_serves_store "${WORK}/new.yml" "${WORK}/garage.env"
OUT="$(compose_serves_store "${WORK}/old.yml" "${WORK}/garage.env" 2>&1 || true)"
check "says garage" grep -q "garage" <<<"${OUT}"

echo "before_the_switch"
check "old release accepted while on minio" compose_serves_store "${WORK}/old.yml" "${WORK}/minio.env"
check "release 1 accepted while on minio" compose_serves_store "${WORK}/new.yml" "${WORK}/minio.env"

echo "rollback_checks_before_changing_anything"
RB="${HERE}/../50-rollback.sh"
guard_line="$({ grep -n 'compose_serves_store' "${RB}" || true; } | head -n 1 | cut -d: -f1)"
checkout_line="$({ grep -n 'git checkout -q "${PREV_SHA}"' "${RB}" || true; } | head -n 1 | cut -d: -f1)"
check "50-rollback.sh runs the guard" test -n "${guard_line}"
check "before it checks out the old commit" test "${guard_line:-999999}" -lt "${checkout_line:-0}"
check "names the way back to MinIO" grep -q "60-switch-storage.sh --back" "${RB}"
check "checks storage after the rollback" grep -q 'readyz_storage_ok "${HEALTH_URL}"' "${RB}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "rollback-guard: all passed"; else echo "rollback-guard: ${FAILS} failed"; exit 1; fi
