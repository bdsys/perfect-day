#!/usr/bin/env bash
# scripts/nuc/60-switch-storage.sh — move the photos from MinIO to Garage, or back
# (tracker row 41; design: docs/superpowers/specs/2026-11-18-garage-design.md).
#
# Usage: sudo ./scripts/nuc/60-switch-storage.sh [--back] [--yes-near-backup]
#
#   (no flag)          MinIO -> Garage
#   --back             Garage -> MinIO (until release 2 removes MinIO)
#   --yes-near-backup  run even within 30 minutes of the 02:17 nightly backup
#
# The site is down for a few minutes. In order:
#   1. takes the deploy/backup lock, so no deploy or nightly backup runs meanwhile;
#   2. refuses if the app already uses the destination, or uses neither store;
#   3. starts both stores and refuses if either is not healthy;
#   4. refuses if the source lacks any object the database refers to (an exact copy
#      of an incomplete store would delete photos from the other one), or if there
#      is not room for a second copy;
#   5. dumps the database to /var/backups/perfect-day/pre-switch-<ts>.sql.gz;
#   6. stops api, worker and beat;
#   7. copies every object, making the destination an exact copy, then checks the
#      two stores' digests match and that a sample of photos decrypts from it;
#   8. points S3_ENDPOINT_URL in /etc/perfect-day/app.env at the destination;
#   9. starts the app, requires /readyz storage "ok", decrypts the sample again
#      through the live settings, and runs the smoke test;
#  10. stops (never removes) the source store;
#  11. releases the lock and runs a backup straight away.
# Any failure before step 8 restarts the app on the source, unchanged. Any failure
# in step 9 points the app back at the source and restarts it. Ctrl-C and a dropped
# ssh session are handled the same way.
#
# Exit: 0 switched, 1 failed (the app is back on the source), 2 refused before
# changing anything, 75 another deploy or backup holds the lock.
#
# Both stores use the key pair in app.env: S3_ACCESS_KEY/S3_SECRET_KEY for Garage,
# MINIO_ROOT_USER/MINIO_ROOT_PASSWORD for MinIO (the same values on the NUC).
# Values are handed to containers by name (-e NAME), never on a command line.
#
# Overrides for the rehearsal and tests (scripts/nuc/tests/switch-storage.test.sh):
# PD_DEPLOY_DIR, PD_ETC, PD_BACKUP_ROOT, PD_LOCK, PD_COMPOSE_PROJECT,
# PD_COMPOSE_PROFILE (default nuc; empty for none), PD_SMOKE_URL, PD_RUN_BACKUP,
# PD_BACKUP_CMD, PD_NOW_HHMM, PD_READY_TIMEOUT.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deploy-lock.sh
source "${HERE}/lib/deploy-lock.sh"

PD_DEPLOY_DIR="${PD_DEPLOY_DIR:-/opt/perfect-day}"
PD_ETC="${PD_ETC:-/etc/perfect-day}"
PD_BACKUP_ROOT="${PD_BACKUP_ROOT:-/var/backups/perfect-day}"
PD_COMPOSE_PROJECT="${PD_COMPOSE_PROJECT:-}"
PD_COMPOSE_PROFILE="${PD_COMPOSE_PROFILE-nuc}"
PD_SMOKE_URL="${PD_SMOKE_URL:-https://perfectday-api.andrewlass.com}"
PD_RUN_BACKUP="${PD_RUN_BACKUP:-1}"
PD_BACKUP_CMD="${PD_BACKUP_CMD:-${HERE}/backup-run.sh}"
PD_READY_TIMEOUT="${PD_READY_TIMEOUT:-90}"
ENV_FILE="${PD_ETC}/app.env"
ENVFILE_PY="${HERE}/lib/envfile.py"
MINIO_URL="http://minio:9000"
GARAGE_URL="http://garage:3900"
BACKUP_AT="02:17"

BACK=0; NEAR_OK=0
for arg in "$@"; do
    case "${arg}" in
        --back) BACK=1 ;;
        --yes-near-backup) NEAR_OK=1 ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown option: ${arg}" >&2; exit 2 ;;
    esac
done

refuse() { echo "switch-storage: REFUSED, nothing changed: $*" >&2; exit 2; }
say() { echo "[switch] $*"; }

sw_compose() {
    local args=()
    [ -n "${PD_COMPOSE_PROJECT}" ] && args+=(-p "${PD_COMPOSE_PROJECT}")
    [ -n "${PD_COMPOSE_PROFILE}" ] && args+=(--profile "${PD_COMPOSE_PROFILE}")
    (cd "${PD_DEPLOY_DIR}" && docker compose "${args[@]}" "$@")
}
getv() { python3 "${ENVFILE_PY}" get "${ENV_FILE}" "$1"; }
getv_or() { getv "$1" 2>/dev/null || printf '%s' "$2"; }
setv() { python3 "${ENVFILE_PY}" set "${ENV_FILE}" "$1" "$2" >/dev/null; }

# ── 1. the lock ─────────────────────────────────────────────────────────────
[ -f "${ENV_FILE}" ] || refuse "${ENV_FILE} not found"
if ! PD_LOCK_WAIT="${PD_LOCK_WAIT:-0}" deploy_lock_take; then
    echo "switch-storage: a deploy or backup is running; try again when it has finished" >&2
    exit 75
fi

# ── 2. which way ────────────────────────────────────────────────────────────
GARAGE_KEY="$(getv S3_ACCESS_KEY)"; GARAGE_SECRET="$(getv S3_SECRET_KEY)"
MINIO_KEY="$(getv_or MINIO_ROOT_USER minioadmin)"; MINIO_SECRET="$(getv_or MINIO_ROOT_PASSWORD minioadmin)"
if [ "${BACK}" = 1 ]; then
    SRC=garage; SRC_URL="${GARAGE_URL}"; SRC_KEY="${GARAGE_KEY}"; SRC_SECRET="${GARAGE_SECRET}"
    DST=minio;  DST_URL="${MINIO_URL}";  DST_KEY="${MINIO_KEY}";  DST_SECRET="${MINIO_SECRET}"
    AGAIN="sudo ./scripts/nuc/60-switch-storage.sh"
else
    SRC=minio;  SRC_URL="${MINIO_URL}";  SRC_KEY="${MINIO_KEY}";  SRC_SECRET="${MINIO_SECRET}"
    DST=garage; DST_URL="${GARAGE_URL}"; DST_KEY="${GARAGE_KEY}"; DST_SECRET="${GARAGE_SECRET}"
    AGAIN="sudo ./scripts/nuc/60-switch-storage.sh --back"
fi
CURRENT="$(getv S3_ENDPOINT_URL 2>/dev/null || true)"
[ "${CURRENT}" = "${DST_URL}" ] && refuse "the app already uses ${DST} (${DST_URL})"
[ "${CURRENT}" = "${SRC_URL}" ] || refuse "S3_ENDPOINT_URL is '${CURRENT}', expected ${SRC_URL}"

# The nightly backup does not wait for the lock: it skips the night. Keep clear.
now="${PD_NOW_HHMM:-$(date +%H%M)}"
mins=$(( 10#${now:0:2} * 60 + 10#${now:2:2} ))
gap=$(( mins - (2 * 60 + 17) )); gap=${gap#-}
if [ "${gap}" -lt 30 ] && [ "${NEAR_OK}" != 1 ]; then
    refuse "it is within 30 minutes of the ${BACKUP_AT} nightly backup; wait, or add --yes-near-backup"
fi

say "Moving photos from ${SRC} to ${DST}."

# ── 3. both stores up ───────────────────────────────────────────────────────
python3 "${ENVFILE_PY}" ensure "${ENV_FILE}" GARAGE_RPC_SECRET hex32 | sed 's/^/[switch] /'
sw_compose up -d --wait "${SRC}" "${DST}" \
    || refuse "the ${SRC} or ${DST} store did not become healthy (see: docker compose logs ${DST})"

# One-off api container aimed at a store. Values travel by name only.
run_on() { # run_on src|dst|live <manage.py args...>
    local which="$1"; shift
    local base=(run --rm --no-deps -T -v "${PD_DEPLOY_DIR}/scripts:/scripts:ro" -e PYTHONPATH=/app)
    case "${which}" in
        src) S3_ENDPOINT_URL="${SRC_URL}" S3_ACCESS_KEY="${SRC_KEY}" S3_SECRET_KEY="${SRC_SECRET}" \
                 sw_compose "${base[@]}" -e S3_ENDPOINT_URL -e S3_ACCESS_KEY -e S3_SECRET_KEY \
                 api python /scripts/manage.py "$@" ;;
        dst) S3_ENDPOINT_URL="${DST_URL}" S3_ACCESS_KEY="${DST_KEY}" S3_SECRET_KEY="${DST_SECRET}" \
                 sw_compose "${base[@]}" -e S3_ENDPOINT_URL -e S3_ACCESS_KEY -e S3_SECRET_KEY \
                 api python /scripts/manage.py "$@" ;;
        live) sw_compose "${base[@]}" api python /scripts/manage.py "$@" ;;
    esac
}
count_of() { jq -r "\"\\(.objects) objects, \\(.bytes) bytes\"" <<<"$1"; }

# ── 4. the source is complete, and there is room ───────────────────────────
if ! missing_out="$(run_on src photos missing 2>&1)"; then
    echo "${missing_out}" >&2
    refuse "${SRC} lacks objects the database refers to; copying it would lose photos"
fi
SRC_DIGEST="$(run_on src photos digest)" || refuse "could not list the ${SRC} store"
src_bytes="$(jq -r .bytes <<<"${SRC_DIGEST}")"
root_dir="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
if [ -n "${root_dir}" ] && [ -d "${root_dir}" ]; then
    avail_kb="$(df -Pk "${root_dir}" | awk 'NR == 2 { print $4 }')"
    if [ $(( avail_kb * 1024 )) -lt $(( src_bytes * 2 )) ]; then
        refuse "not enough disk space under ${root_dir}: ${avail_kb} KB free, a copy of ${src_bytes} bytes needs twice that"
    fi
else
    say "Could not find Docker's data folder to check free space; going on."
fi
say "Source: $(count_of "${SRC_DIGEST}"), all referenced objects present."

# ── 5. dump ─────────────────────────────────────────────────────────────────
mkdir -p "${PD_BACKUP_ROOT}"
DUMP="${PD_BACKUP_ROOT}/pre-switch-$(date +%Y%m%d-%H%M%S).sql.gz"
( umask 077; sw_compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "${DUMP}" ) \
    || refuse "could not dump the database to ${DUMP}"
say "Database dumped to ${DUMP}"

# ── 6. stop the app; from here every exit puts it back ──────────────────────
STOPPED=0; FLIPPED=0; DONE=0
on_exit() {
    local rc=$?
    [ "${DONE}" = 1 ] && return
    [ "${STOPPED}" = 1 ] || return
    set +e
    if [ "${FLIPPED}" = 1 ]; then
        setv S3_ENDPOINT_URL "${SRC_URL}"
        echo "switch-storage: pointed the app back at ${SRC}" >&2
    fi
    sw_compose up -d --no-deps api worker beat >/dev/null 2>&1
    echo "switch-storage: FAILED (exit ${rc}). The app is running on ${SRC} again, unchanged." >&2
    echo "  The database dump from before: ${DUMP}" >&2
    [ "${rc}" -eq 0 ] && exit 1
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
say "Stopping the app (the site is down from now)..."
sw_compose stop api worker beat
STOPPED=1

# ── 7. copy and check ───────────────────────────────────────────────────────
IMPORT_ERR="$(mktemp)"
say "Copying..."
if ! run_on src photos export | run_on dst photos import --exact 2>"${IMPORT_ERR}"; then
    cat "${IMPORT_ERR}" >&2; rm -f "${IMPORT_ERR}"
    echo "switch-storage: the copy failed" >&2; exit 1
fi
IMPORT_STATS="$(tail -n 1 "${IMPORT_ERR}")"; rm -f "${IMPORT_ERR}"
SRC_AFTER="$(run_on src photos digest)"
DST_DIGEST="$(run_on dst photos digest)"
if [ "$(jq -cS . <<<"${SRC_AFTER}")" != "$(jq -cS . <<<"${DST_DIGEST}")" ]; then
    echo "switch-storage: the stores differ after the copy:" >&2
    echo "  ${SRC}: $(count_of "${SRC_AFTER}")" >&2
    echo "  ${DST}: $(count_of "${DST_DIGEST}")" >&2
    exit 1
fi
if ! run_on dst photos verify; then
    echo "switch-storage: photos copied to ${DST} did not decrypt" >&2; exit 1
fi
say "Copied: ${IMPORT_STATS}; both stores hold $(count_of "${DST_DIGEST}")."

# ── 8. flip ─────────────────────────────────────────────────────────────────
setv S3_ENDPOINT_URL "${DST_URL}"
FLIPPED=1

# ── 9. start and check through the running app ──────────────────────────────
sw_compose up -d --no-deps api worker beat
ready_probe='import json, urllib.request
try:
    body = json.load(urllib.request.urlopen("http://localhost:8000/readyz", timeout=5))
except Exception:
    print("down")
else:
    print(body["checks"]["storage"])'
storage="down"; waited=0
while :; do
    storage="$(sw_compose exec -T api python -c "${ready_probe}" 2>/dev/null || echo down)"
    [ "${storage}" = ok ] && break
    [ "${waited}" -ge "${PD_READY_TIMEOUT}" ] && break
    sleep 1; waited=$(( waited + 1 ))
done
if [ "${storage}" != ok ]; then
    echo "switch-storage: after the switch /readyz says storage '${storage}'" >&2; exit 1
fi
run_on live photos verify || { echo "switch-storage: photos did not decrypt through the app" >&2; exit 1; }
SMOKE_CLEANUP=1 "${PD_DEPLOY_DIR}/scripts/smoke-test.sh" "${PD_SMOKE_URL}" \
    || { echo "switch-storage: the smoke test failed after the switch" >&2; exit 1; }
DONE=1
trap - EXIT INT TERM HUP
say "The app is running on ${DST}; the site is back."

# ── 10. stop the old store (its data stays) ─────────────────────────────────
sw_compose stop "${SRC}"
say "Stopped ${SRC}. Its data is untouched."

# ── 11. a backup now ────────────────────────────────────────────────────────
exec 8>&-   # release the lock, which the backup takes
BACKUP_RESULT="not run (PD_RUN_BACKUP=0)"
if [ "${PD_RUN_BACKUP}" = 1 ]; then
    say "Taking a backup from ${DST} now..."
    if "${PD_BACKUP_CMD}"; then BACKUP_RESULT="ok"
    else BACKUP_RESULT="FAILED (exit $?): see /var/log/perfect-day/backup.log"; fi
fi

echo ""
echo "Switched photo storage to ${DST}."
echo "  Objects:  $(count_of "${DST_DIGEST}")"
echo "  Copy:     ${IMPORT_STATS}"
echo "  Dump:     ${DUMP}"
echo "  Backup:   ${BACKUP_RESULT}"
echo "  To go back: ${AGAIN}"
