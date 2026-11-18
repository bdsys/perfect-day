#!/usr/bin/env bash
# scripts/nuc/restore.sh — restore one night's backup (tracker row 36).
#
# Usage: sudo ./scripts/nuc/restore.sh <YYYY-MM-DD> [--from-nas] [--into <project>]
#            [--with-config | --config-only] [--key <path>] [--source-dir <dir>]
#
#   (no --into)       PRODUCTION: asks you to type the date, takes a safety dump to
#                     /var/backups/perfect-day/pre-restore-<ts>.sql.gz, stops api,
#                     worker and beat, replaces the database and imports the photos,
#                     starts them again and runs the smoke test.
#   --into <project>  restores into another compose project's postgres and photo
#                     store (e.g. perfect-day-drill); starts only those two. The
#                     store is the one S3_ENDPOINT_URL names (garage or minio).
#   --from-nas        fetches the night from rsync://192.168.1.112/Backups/perfect-day/
#   --with-config     also puts /etc/perfect-day back (asks you to type "replace config";
#                     the current one is kept as /etc/perfect-day.pre-restore-<ts>).
#                     The live S3_ENDPOINT_URL is kept: it says which photo store
#                     this machine runs, not a secret, so a night from before the
#                     MinIO -> Garage switch restores here unchanged (row 41).
#   --config-only     only /etc/perfect-day, no Docker: step one on a new machine,
#                     before 20-deploy.sh (see deploy/nuc-ops.md § B7). Never run
#                     10-secrets.sh on a machine you are restoring.
#   --key <path>      the backup's private key (default /etc/perfect-day/backup.key)
set -euo pipefail
umask 077

HERE="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib/backup.sh
source "${HERE}/lib/backup.sh"

die() { echo "restore: $*" >&2; exit 1; }

DATE="${1:-}"; [ $# -gt 0 ] && shift
FROM_NAS=0; INTO=""; WITH_CONFIG=0; CONFIG_ONLY=0; KEY="${PD_KEY}"; SOURCE_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --from-nas) FROM_NAS=1 ;;
        --into) INTO="${2:?--into needs a project name}"; shift ;;
        --with-config) WITH_CONFIG=1 ;;
        --config-only) CONFIG_ONLY=1 ;;
        --key) KEY="${2:?--key needs a path}"; shift ;;
        --source-dir) SOURCE_DIR="${2:?--source-dir needs a path}"; shift ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

[[ "${DATE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "give the night as YYYY-MM-DD"
[ "${INTO}" = "perfect-day" ] && die "--into perfect-day is production; leave out --into to restore production"
[ -n "${INTO}" ] && [ "${WITH_CONFIG}" = 1 ] && die "--with-config only applies to this machine's own config, not --into"

# ── the night's files ───────────────────────────────────────────────────────
FETCHED=""
cleanup() { [ -n "${FETCHED}" ] && rm -rf "${FETCHED}"; return 0; }
trap cleanup EXIT

if [ -n "${SOURCE_DIR}" ]; then
    SRC="${SOURCE_DIR}"
elif [ "${FROM_NAS}" = 1 ]; then
    mkdir -p "${PD_BACKUP_ROOT}"
    FETCHED="$(mktemp -d "${PD_BACKUP_ROOT}/restore.XXXX")"
    rsync -a --timeout=60 "${PD_NAS_URL}${DATE}/" "${FETCHED}/" || die "could not fetch ${DATE} from the NAS"
    SRC="${FETCHED}"
else
    SRC="${PD_NIGHTLY}/${DATE}"
fi
[ -f "${SRC}/manifest.json" ] || die "no backup for ${DATE} in ${SRC}"

for f in db.sql.gz.age photos.tar.age config.tar.age; do
    want="$(jq -r --arg f "${f}" '.files[$f].sha256' "${SRC}/manifest.json")"
    got="$(sha256sum "${SRC}/${f}" 2>/dev/null | cut -d' ' -f1 || true)"
    [ -n "${got}" ] && [ "${got}" = "${want}" ] || die "${f} does not match its checksum in manifest.json; not restoring"
done

# Prove the whole night decrypts and is complete before anything is changed: a
# wrong or mis-pasted key must fail here, not after production's database is gone.
backup_check_files "${SRC}" "${KEY}" || die "not restoring: the backup for ${DATE} is not usable (see above)"

decrypt() { age -d -i "${KEY}" < "${SRC}/$1"; }

# ── config ──────────────────────────────────────────────────────────────────
if [ "${CONFIG_ONLY}" = 1 ] || [ "${WITH_CONFIG}" = 1 ]; then
    echo "This puts ${PD_ETC} back as it was on ${DATE}. Type \"replace config\" to go on:"
    read -r answer || true
    [ "${answer:-}" = "replace config" ] || die "config not restored"
    if [ -d "${PD_ETC}" ]; then
        saved="${PD_ETC}.pre-restore-$(date +%Y%m%d-%H%M%S)"
        cp -a "${PD_ETC}" "${saved}"
        echo "Current config kept at ${saved}"
    fi
    live_endpoint="$(python3 "${PD_ENVFILE_PY}" get "${PD_ETC}/app.env" S3_ENDPOINT_URL 2>/dev/null || true)"
    decrypt config.tar.age | tar -C / -xf -
    echo "Config restored."
    if [ -n "${live_endpoint}" ]; then
        python3 "${PD_ENVFILE_PY}" set "${PD_ETC}/app.env" S3_ENDPOINT_URL "${live_endpoint}" >/dev/null
        echo "  kept the storage address this machine uses (${live_endpoint})"
    else
        # A new machine: keep the night's store only if this compose file defines it.
        svc="$(storage_service_from_env "${PD_ETC}/app.env" 2>/dev/null || echo unknown)"
        if ! grep -qE "^  ${svc}:" "${PD_DEPLOY_DIR}/docker-compose.yml" 2>/dev/null; then
            python3 "${PD_ENVFILE_PY}" set "${PD_ETC}/app.env" S3_ENDPOINT_URL http://garage:3900 >/dev/null
            echo "  the backup's photo store is ${svc}, but there is no ${svc} service here;"
            echo "  S3_ENDPOINT_URL set to http://garage:3900"
        fi
    fi
    [ "${CONFIG_ONLY}" = 1 ] && exit 0
fi

PSQL_RESTORE='psql -q -o /dev/null -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
IMPORT=(run --rm --no-deps -T -v "${PD_DEPLOY_DIR}/scripts:/scripts:ro" -e PYTHONPATH=/app
        api python /scripts/manage.py photos import)

# ── into another project (the drill) ────────────────────────────────────────
if [ -n "${INTO}" ]; then
    compose_p() { (cd "${PD_DEPLOY_DIR}" && docker compose -p "${INTO}" "$@"); }
    # The api service has no image name, so a new project would build its own;
    # borrow production's built image under this project's name instead.
    docker tag perfect-day-api:latest "${INTO}-api:latest"
    store="$(storage_service_from_env "${PD_ETC}/app.env")" || die "not restoring: see above"
    compose_p up -d --wait postgres "${store}"
    decrypt db.sql.gz.age | gunzip | compose_p exec -T postgres sh -c "${PSQL_RESTORE}"
    decrypt photos.tar.age | compose_p "${IMPORT[@]}"
    echo "Restored ${DATE} into project ${INTO}."
    exit 0
fi

# ── production ──────────────────────────────────────────────────────────────
echo "This REPLACES production's database and photos with the backup from ${DATE}."
echo "Type the date (${DATE}) to go on:"
read -r answer || true
[ "${answer:-}" = "${DATE}" ] || die "nothing changed"

safety="${PD_BACKUP_ROOT}/pre-restore-$(date +%Y%m%d-%H%M%S).sql.gz"
pd_compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "${safety}"
echo "Safety dump of the current database: ${safety}"

# From here on production is being changed. If any step fails, start the app again
# and say exactly how to put the database back as it was.
PROD_DONE=0
on_prod_exit() {
    local rc=$?
    cleanup
    [ "${PROD_DONE}" = 1 ] && return
    set +e
    echo "" >&2
    echo "restore: FAILED (exit ${rc}). Production may be half-restored." >&2
    echo "  To put the database back as it was before this restore:" >&2
    echo "    cd ${PD_DEPLOY_DIR} && sudo docker compose --profile nuc stop api worker beat" >&2
    echo "    sudo docker compose --profile nuc exec -T postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d postgres -c \"DROP DATABASE IF EXISTS \\\"\$POSTGRES_DB\\\" WITH (FORCE)\" -c \"CREATE DATABASE \\\"\$POSTGRES_DB\\\" OWNER \\\"\$POSTGRES_USER\\\"\"'" >&2
    echo "    sudo gunzip -c ${safety} | sudo docker compose --profile nuc exec -T postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" >&2
    echo "  Starting api, worker and beat again now." >&2
    pd_compose up -d api worker beat
}
trap on_prod_exit EXIT

pd_compose stop api worker beat
pd_compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""'
decrypt db.sql.gz.age | gunzip | pd_compose exec -T postgres sh -c "${PSQL_RESTORE}"
decrypt photos.tar.age | pd_compose "${IMPORT[@]}"
pd_compose up -d api worker beat
SMOKE_CLEANUP=1 "${PD_DEPLOY_DIR}/scripts/smoke-test.sh" "${PD_SMOKE_URL:-https://perfectday-api.andrewlass.com}"
PROD_DONE=1
echo "Production restored from ${DATE}."
