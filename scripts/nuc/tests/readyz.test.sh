#!/usr/bin/env bash
# Tests for scripts/nuc/lib/readyz.sh: a deploy counts as healthy only when the
# readiness check answers AND says photo storage is ok (tracker row 41; /readyz
# itself treats storage as a warning, so a 200 alone proves nothing about photos).
# curl is a PATH shim. Usage: bash scripts/nuc/tests/readyz.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HERE}/../lib/readyz.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/curl" <<'SHIM'
#!/usr/bin/env bash
[ -n "${CURL_FAIL:-}" ] && exit 7
printf '%s' "${BODY}"
SHIM
chmod +x "${WORK}/bin/curl"
export PATH="${WORK}/bin:${PATH}"
# shellcheck source=../lib/readyz.sh
source "${LIB}"
fails() { ! readyz_storage_ok "$@" 2>/dev/null; }

echo "storage_ok"
export BODY='{"status":"ok","checks":{"postgres":"ok","redis":"ok","storage":"ok"}}'
check "ok when storage is ok" readyz_storage_ok http://x/readyz

echo "storage_error"
export BODY='{"status":"ok","checks":{"postgres":"ok","redis":"ok","storage":"error"}}'
RC=0; OUT="$(readyz_storage_ok http://x/readyz 2>&1)" || RC=$?
check "fails when storage reports error" test "${RC}" -ne 0
check "says storage" grep -q "storage" <<<"${OUT}"

echo "no_storage_check"
export BODY='{"status":"ok","checks":{"postgres":"ok","minio":"ok"}}'
check "fails when the check is missing" fails http://x/readyz

echo "unreachable"
export CURL_FAIL=1
check "fails when readyz does not answer" fails http://x/readyz
unset CURL_FAIL

echo "not_json"
export BODY='<html>bad gateway</html>'
check "fails on a non-JSON answer" fails http://x/readyz

echo "deploy_uses_it"
UPDATE="${HERE}/../40-update.sh"
check "40-update.sh sources the helper" grep -q 'lib/readyz.sh' "${UPDATE}"
check "40-update.sh requires storage ok" grep -q 'readyz_storage_ok "${HEALTH_URL}"' "${UPDATE}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "readyz: all passed"; else echo "readyz: ${FAILS} failed"; exit 1; fi
