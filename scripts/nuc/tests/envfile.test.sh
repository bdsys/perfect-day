#!/usr/bin/env bash
# Tests for scripts/nuc/lib/envfile.py, the safe one-setting env-file editor
# (tracker row 41). Temp files only.
# Usage: bash scripts/nuc/tests/envfile.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVFILE="${HERE}/../lib/envfile.py"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}
ef() { python3 "${ENVFILE}" "$@"; }

setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    REAL="${WORK}/etc/app.env"
    mkdir -p "${WORK}/etc" "${WORK}/deploy"
    printf '%s\n' '# Perfect Day' 'MASTER_SECRET=abc123' '#S3_ENDPOINT_URL=http://old:1' \
        'S3_ENDPOINT_URL=http://minio:9000' 'DATABASE_URL=postgresql://u:p=q@h/db' > "${REAL}"
    chmod 600 "${REAL}"
    ln -s "${REAL}" "${WORK}/deploy/.env"
    LINK="${WORK}/deploy/.env"
}

echo "set_through_symlink"
setup
before_other="$(grep -v '^S3_ENDPOINT_URL=' "${REAL}")"
OUT="$(ef set "${LINK}" S3_ENDPOINT_URL http://garage:3900)"
check "the link is still a link" test -L "${LINK}"
check "the target changed" grep -qx 'S3_ENDPOINT_URL=http://garage:3900' "${REAL}"
check "exactly one live line" test "$(grep -c '^S3_ENDPOINT_URL=' "${REAL}")" -eq 1
check "mode 600 kept" test "$(stat -c %a "${REAL}")" = "600"
check "a backup exists" bash -c 'ls "$1".bak-* >/dev/null 2>&1' _ "${REAL}"
check "backup holds the old value" bash -c 'grep -qx "S3_ENDPOINT_URL=http://minio:9000" "$1".bak-*' _ "${REAL}"
check "other lines byte-identical" test "$(grep -v '^S3_ENDPOINT_URL=' "${REAL}")" = "${before_other}"
check "commented line untouched" grep -qx '#S3_ENDPOINT_URL=http://old:1' "${REAL}"
check "says where" grep -q "S3_ENDPOINT_URL set in ${REAL}" <<<"${OUT}"

echo "set_appends_missing_key"
setup
ef set "${REAL}" GARAGE_X value-1 >/dev/null
check "appended once" test "$(grep -c '^GARAGE_X=value-1$' "${REAL}")" -eq 1
check "last line" test "$(tail -n 1 "${REAL}")" = "GARAGE_X=value-1"

echo "duplicate_key_refused"
setup
echo 'S3_ENDPOINT_URL=http://dup:1' >> "${REAL}"
sum="$(sha256sum "${REAL}")"
RC=0; ef set "${REAL}" S3_ENDPOINT_URL http://garage:3900 2>/dev/null || RC=$?
check "set exits 4" test "${RC}" -eq 4
check "file unchanged" test "$(sha256sum "${REAL}")" = "${sum}"
RC=0; ef get "${REAL}" S3_ENDPOINT_URL >/dev/null 2>&1 || RC=$?
check "get exits 4" test "${RC}" -eq 4

echo "get_values"
setup
check "value with = round-trips" test "$(ef get "${LINK}" DATABASE_URL)" = "postgresql://u:p=q@h/db"
RC=0; ef get "${REAL}" NOPE >/dev/null 2>&1 || RC=$?
check "absent exits 3" test "${RC}" -eq 3
check "commented line is not a value" test "$(ef get "${REAL}" S3_ENDPOINT_URL)" = "http://minio:9000"

echo "ensure_generates_once_and_never_prints"
setup
OUT1="$(ef ensure "${LINK}" GARAGE_RPC_SECRET hex32)"
value="$(sed -n 's/^GARAGE_RPC_SECRET=//p' "${REAL}")"
check "says generated" test "${OUT1}" = "GARAGE_RPC_SECRET generated"
check "64 hex characters" bash -c '[[ "$1" =~ ^[0-9a-f]{64}$ ]]' _ "${value}"
check "value not printed" bash -c '! grep -q "$1" <<<"$2"' _ "${value}" "${OUT1}"
check "link kept" test -L "${LINK}"
check "mode 600 kept" test "$(stat -c %a "${REAL}")" = "600"
OUT2="$(ef ensure "${LINK}" GARAGE_RPC_SECRET hex32)"
check "second run says kept" test "${OUT2}" = "GARAGE_RPC_SECRET kept"
check "value unchanged" test "$(sed -n 's/^GARAGE_RPC_SECRET=//p' "${REAL}")" = "${value}"
RC=0; ef ensure "${REAL}" X nope >/dev/null 2>&1 || RC=$?
check "unknown generator exits 2" test "${RC}" -eq 2

echo "unset_removes_the_live_line"
setup
before_other="$(grep -v '^DATABASE_URL=' "${REAL}")"
OUT="$(ef unset "${LINK}" DATABASE_URL)"
check "line gone" bash -c '! grep -q "^DATABASE_URL=" "$1"' _ "${REAL}"
check "other lines byte-identical" test "$(cat "${REAL}")" = "${before_other}"
check "link kept" test -L "${LINK}"
check "mode 600 kept" test "$(stat -c %a "${REAL}")" = "600"
check "a backup exists" bash -c 'ls "$1".bak-* >/dev/null 2>&1' _ "${REAL}"
check "says so" grep -q "DATABASE_URL removed" <<<"${OUT}"
check "value not printed" bash -c '! grep -q "p=q" <<<"$1"' _ "${OUT}"
OUT="$(ef unset "${REAL}" DATABASE_URL)"
check "absent is fine" grep -q "DATABASE_URL was not set" <<<"${OUT}"
check "commented line untouched" grep -qx '#S3_ENDPOINT_URL=http://old:1' "${REAL}"

echo "missing_file"
RC=0; ef get "${TOP}/none.env" X >/dev/null 2>&1 || RC=$?
check "exits 2" test "${RC}" -eq 2

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "envfile: all passed"; else echo "envfile: ${FAILS} failed"; exit 1; fi
