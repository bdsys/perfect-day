#!/usr/bin/env bash
# Tests for scripts/nuc/restore.sh and scripts/nuc/drill.sh (tracker row 36).
# docker, rsync and age are PATH shims that log their arguments; the "NAS" is a
# local folder. Never touches /var, /etc or Docker.
# Usage: bash scripts/nuc/tests/restore.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESTORE="${HERE}/../restore.sh"
DRILL="${HERE}/../drill.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    mkdir -p "${WORK}/bin" "${WORK}/etc/perfect-day" "${WORK}/backups/nightly" "${WORK}/log" \
        "${WORK}/deploy/scripts" "${WORK}/nas" "${WORK}/calls" "${WORK}/objects"
    # The NUC before the switch (row 41): the app uses MinIO.
    printf "MASTER_SECRET=live\nS3_ENDPOINT_URL=http://minio:9000\n" > "${WORK}/etc/perfect-day/app.env"
    printf "services:\n  postgres:\n    image: p\n  minio:\n    image: m\n  garage:\n    image: g\n" \
        > "${WORK}/deploy/docker-compose.yml"
    echo "PRIVATE" > "${WORK}/etc/perfect-day/backup.key"
    printf 'one' > "${WORK}/objects/a.bin"; printf 'two!' > "${WORK}/objects/b.bin"
    printf '#!/usr/bin/env bash\necho "smoke" >> "%s/calls/order"\n' "${WORK}" > "${WORK}/deploy/scripts/smoke-test.sh"
    chmod +x "${WORK}/deploy/scripts/smoke-test.sh"

    cat > "${WORK}/bin/age" <<'EOF'
#!/usr/bin/env bash
echo "age $*" >> "${CALLS}/age"
# A key file holding WRONG cannot decrypt anything, like a mis-pasted key.
if [ "$1" = "-d" ] && [ "$(cat "$3" 2>/dev/null)" = "WRONG" ]; then
    echo "age: error: no identity matched any of the recipients" >&2; exit 1
fi
cat
EOF
    # docker: logs every call and a one-word step to calls/order.
    cat > "${WORK}/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${CALLS}/docker"
a="$*"
step() { echo "$1" >> "${CALLS}/order"; }
case "${a}" in
    *"ps --services"*) printf '%b' "${PS_SERVICES:-minio\npostgres\n}"; exit 0 ;;
    "ps --filter"*) printf '%b' "${PORTS:-5432/tcp\n9000/tcp\n}"; exit 0 ;;
    *pg_isready*) exit 0 ;;
    *pg_dump*) step dump; printf -- '-- dump\n'; exit 0 ;;
    *"DROP DATABASE"*) step drop; exit 0 ;;
    *"-At -c"*) echo "${ROWS:-1|2|3|4}"; exit 0 ;;
    *psql*) step restore; cat > /dev/null; [ -n "${RESTORE_FAIL:-}" ] && { echo "psql: ERROR" >&2; exit 3; }; exit 0 ;;
    *"photos import"*) step import; cat > /dev/null; echo '{"objects": 2, "bytes": 7}' >&2; exit 0 ;;
    *"photos export"*) tar -C "${OBJECTS}" -cf - a.bin b.bin; exit 0 ;;
    *"photos verify"*) step verify; echo "checked=3 failures=0"; exit "${VERIFY_EXIT:-0}" ;;
    *" stop "*) step stop; exit 0 ;;
    *" down "*) step down; exit 0 ;;
    *" up "*) step up; exit 0 ;;
esac
exit 0
EOF
    # rsync: maps rsync://nas.invalid/... onto the local "NAS" folder.
    cat > "${WORK}/bin/rsync" <<'EOF'
#!/usr/bin/env bash
echo "rsync $*" >> "${CALLS}/rsync"
map() { echo "${1/rsync:\/\/nas.invalid\//${NAS}/}"; }
if [[ " $* " == *" --list-only "* ]]; then
    ls -1 "$(map "${@: -1}")" | sed 's/^/drwx------ 4096 2026\/11\/18 02:17:00 /'
    exit 0
fi
src="$(map "${@: -2:1}")"; dst="${@: -1}"
mkdir -p "${dst}"; cp -a "${src%/}/." "${dst}"
EOF
    chmod +x "${WORK}/bin/"*

    export PATH="${WORK}/bin:${PATH}" CALLS="${WORK}/calls" NAS="${WORK}/nas" OBJECTS="${WORK}/objects"
    export PD_BACKUP_ROOT="${WORK}/backups" PD_ETC="${WORK}/etc/perfect-day" \
        PD_LOG_DIR="${WORK}/log" PD_DEPLOY_DIR="${WORK}/deploy" \
        PD_NAS_URL="rsync://nas.invalid/Backups/perfect-day/nightly/"
    unset PS_SERVICES PORTS ROWS VERIFY_EXIT RESTORE_FAIL
    touch "${CALLS}/docker" "${CALLS}/order" "${CALLS}/rsync" "${CALLS}/age"
    make_night 2026-11-18 "${WORK}/backups/nightly/2026-11-18"
    mkdir -p "${NAS}/Backups/perfect-day/nightly"
    cp -a "${WORK}/backups/nightly/2026-11-18" "${NAS}/Backups/perfect-day/nightly/"
}

# make_night <date> <dir>: a night as backup-run.sh writes it (age is cat here).
make_night() {
    local night="$1" dir="$2" f files='{}'
    mkdir -p "${dir}"
    printf -- '-- dump\n-- PostgreSQL database dump complete\n' | gzip > "${dir}/db.sql.gz.age"
    tar -C "${OBJECTS}" -cf "${dir}/photos.tar.age" a.bin b.bin
    tar -C / -cf "${dir}/config.tar.age" --exclude="${PD_ETC#/}/backup.key" "${PD_ETC#/}"
    for f in db.sql.gz.age photos.tar.age config.tar.age; do
        files="$(jq -c --arg f "${f}" --arg s "$(sha256sum "${dir}/${f}" | cut -d' ' -f1)" \
            --argjson b "$(stat -c %s "${dir}/${f}")" '. + {($f): {bytes: $b, sha256: $s}}' <<<"${files}")"
    done
    jq -n --arg n "${night}" --argjson files "${files}" \
        '{night: $n, files: $files, objects: 2, object_bytes: 7, rows: {users: 1, diaries: 2, entries: 3, photos: 4}}' \
        > "${dir}/manifest.json"
}

order() { tr '\n' ' ' < "${CALLS}/order" | sed 's/ $//'; }

# ── restore.sh ───────────────────────────────────────────────────────────────
echo "into_production_project_name_refused"
setup
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 --into perfect-day </dev/null 2>&1) || RC=$?
check "exits 1" test "${RC}" -eq 1
check "says why" grep -qi "production" <<<"${OUT}"
check "no docker calls" test ! -s "${CALLS}/docker"

echo "bad date refused"
setup
RC=0; bash "${RESTORE}" 18-11-2026 --into perfect-day-drill </dev/null >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1

echo "production_restore_needs_typed_date"
setup
RC=0; bash "${RESTORE}" 2026-11-18 <<<"2026-11-17" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "never stopped the app" bash -c '! grep -q " stop " "$1"' _ "${CALLS}/docker"

echo "production_restore_order"
setup
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 <<<"2026-11-18" 2>&1) || RC=$?
check "exits 0" test "${RC}" -eq 0
check "dump → stop → drop → restore → import → up → smoke" test "$(order)" = "dump stop drop restore import up smoke"
check "stops only api, worker and beat" grep -q "stop api worker beat" "${CALLS}/docker"
check "the safety dump is kept and private" \
    bash -c 'f=$(ls "$1"/pre-restore-*.sql.gz) && [ "$(stat -c %a "$f")" = 600 ]' _ "${PD_BACKUP_ROOT}"
check "decrypts with the backup key" grep -q -- "-d -i ${PD_ETC}/backup.key" "${CALLS}/age"

echo "wrong_key_changes_nothing"
setup
echo "WRONG" > "${WORK}/wrong.key"
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 --key "${WORK}/wrong.key" <<<"2026-11-18" 2>&1) || RC=$?
check "exits 1" test "${RC}" -eq 1
check "says the backup could not be decrypted" grep -qi "decrypt" <<<"${OUT}"
check "never asked to confirm" bash -c '! grep -q "Type the date" <<<"$1"' _ "${OUT}"
check "no docker calls: production untouched" test ! -s "${CALLS}/docker"

echo "restore_failure_tells_how_to_recover"
setup
export RESTORE_FAIL=1
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 <<<"2026-11-18" 2>&1) || RC=$?
check "fails" test "${RC}" -ne 0
check "names the safety dump" grep -q "pre-restore-" <<<"${OUT}"
check "shows how to load it back" grep -q "gunzip -c" <<<"${OUT}"
check "starts the app again" test "$(order)" = "dump stop drop restore up"

echo "--key overrides the key"
setup
echo "OTHER" > "${WORK}/other.key"
bash "${RESTORE}" 2026-11-18 --into perfect-day-drill --key "${WORK}/other.key" </dev/null >/dev/null 2>&1
check "uses the given key" grep -q -- "-i ${WORK}/other.key" "${CALLS}/age"

echo "checksum_mismatch_aborts"
setup
echo "tampered" >> "${PD_BACKUP_ROOT}/nightly/2026-11-18/photos.tar.age"
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 --into perfect-day-drill </dev/null 2>&1) || RC=$?
check "exits 1" test "${RC}" -eq 1
check "names the file" grep -q "photos.tar.age" <<<"${OUT}"
check "no docker calls" test ! -s "${CALLS}/docker"

echo "--from-nas fetches the night"
setup
rm -rf "${PD_BACKUP_ROOT}/nightly/2026-11-18"
RC=0; bash "${RESTORE}" 2026-11-18 --from-nas --into perfect-day-drill </dev/null >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "fetched from the NAS URL" grep -q "rsync://nas.invalid/Backups/perfect-day/nightly/2026-11-18/" "${CALLS}/rsync"
check "restored and imported" test "$(order)" = "up restore import"
check "removes its fetched copy" test -z "$(find "${PD_BACKUP_ROOT}" -maxdepth 1 -name 'restore.*')"

echo "into a project: never touches production"
setup
bash "${RESTORE}" 2026-11-18 --into perfect-day-drill </dev/null >/dev/null 2>&1
check "every compose call names the project" bash -c '! grep "compose" "$1" | grep -qv -- "-p perfect-day-drill"' _ "${CALLS}/docker"
check "starts postgres and the store the app uses (minio)" grep -q "up -d --wait postgres minio" "${CALLS}/docker"
check "never --profile nuc" bash -c '! grep -q -- "--profile nuc" "$1"' _ "${CALLS}/docker"
check "reuses production's api image instead of building one" grep -q "tag perfect-day-api:latest perfect-day-drill-api:latest" "${CALLS}/docker"
check "tags before the import runs" bash -c 'grep -n "" "$1" | grep -E "tag perfect-day-api|photos import" | head -1 | grep -q " tag "' _ "${CALLS}/docker"

echo "with_config_saves_old_etc_first"
setup
make_night 2026-11-18 "${PD_BACKUP_ROOT}/nightly/2026-11-18"   # holds MASTER_SECRET=live
echo "MASTER_SECRET=changed" > "${PD_ETC}/app.env"
RC=0; bash "${RESTORE}" 2026-11-18 --config-only <<<"replace config" >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "app.env is the backed-up one" grep -qx "MASTER_SECRET=live" "${PD_ETC}/app.env"
check "the old config is saved beside it" bash -c 'grep -qx "MASTER_SECRET=changed" "$1".pre-restore-*/app.env' _ "${PD_ETC}"
check "config-only needs no Docker" test ! -s "${CALLS}/docker"

echo "config restore needs typed confirmation"
setup
echo "MASTER_SECRET=changed" > "${PD_ETC}/app.env"
RC=0; bash "${RESTORE}" 2026-11-18 --config-only <<<"no" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "app.env untouched" grep -qx "MASTER_SECRET=changed" "${PD_ETC}/app.env"

# ── drill.sh ─────────────────────────────────────────────────────────────────
echo "drill: a good backup"
setup
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "exits 0" test "${RC}" -eq 0
check "prints DRILL PASSED" grep -q "DRILL PASSED" <<<"${OUT}"
check "picked the newest night on the NAS" grep -q "nightly/2026-11-18/" "${CALLS}/rsync"
check "ran photos verify" grep -q "photos verify" "${CALLS}/docker"
check "tore the project down with its volumes" grep -q -- "-p perfect-day-drill down -v" "${CALLS}/docker"
check "removes the borrowed image tag" grep -q "image rm perfect-day-drill-api:latest" "${CALLS}/docker"

echo "drill_starts_only_postgres_and_the_store"
setup
export PS_SERVICES='api\nminio\npostgres\n'
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails when anything else runs" test "${RC}" -ne 0
check "says so" grep -q "DRILL FAILED" <<<"${OUT}"

echo "drill fails if a port is published"
setup
export PORTS='0.0.0.0:5432->5432/tcp\n'
RC=0; bash "${DRILL}" >/dev/null 2>&1 || RC=$?
check "fails" test "${RC}" -ne 0

echo "drill_tears_down_on_failure"
setup
export VERIFY_EXIT=1
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails" test "${RC}" -ne 0
check "still tore down" grep -q -- "-p perfect-day-drill down -v" "${CALLS}/docker"
check "names photos verify" grep -qi "verify" <<<"${OUT}"

echo "drill fails on row count mismatch"
setup
export ROWS="1|2|3|5"
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails" test "${RC}" -ne 0
check "names photos rows" grep -q "photos" <<<"${OUT}"

echo "drill_fails_on_config_diff"
setup
echo "MASTER_SECRET=different" > "${PD_ETC}/app.env"
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails" test "${RC}" -ne 0
check "says app.env differs" grep -q "app.env" <<<"${OUT}"
check "never prints a secret's value" bash -c '! grep -q "different\|=live" <<<"$1"' _ "${OUT}"

# ── row 41: the store follows S3_ENDPOINT_URL; every night stays restorable ──
set_live_endpoint() { python3 "${HERE}/../lib/envfile.py" set "${PD_ETC}/app.env" S3_ENDPOINT_URL "$1" >/dev/null; }

echo "into_starts_storage_named_by_endpoint"
setup
set_live_endpoint http://garage:3900
bash "${RESTORE}" 2026-11-18 --into perfect-day-drill </dev/null >/dev/null 2>&1
check "starts postgres and garage" grep -q "up -d --wait postgres garage" "${CALLS}/docker"
check "not minio" bash -c '! grep -q "up -d --wait postgres minio" "$1"' _ "${CALLS}/docker"

echo "drill_expects_postgres_and_endpoint_service"
setup
set_live_endpoint http://garage:3900
make_night 2026-11-18 "${NAS}/Backups/perfect-day/nightly/2026-11-18"   # a night after the switch
export PS_SERVICES='garage\npostgres\n'
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "passes with garage and postgres" test "${RC}" -eq 0
check "says DRILL PASSED" grep -q "DRILL PASSED" <<<"${OUT}"
export PS_SERVICES='minio\npostgres\n'
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails when the other store ran" test "${RC}" -ne 0

echo "drill_ignores_only_the_endpoint_line"
setup   # the night on the NAS says minio (taken before the switch)
set_live_endpoint http://garage:3900
export PS_SERVICES='garage\npostgres\n'
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "a night from before the switch still passes" test "${RC}" -eq 0
setup
set_live_endpoint http://garage:3900
echo "MASTER_SECRET=different" >> "${PD_ETC}/app.env"
python3 - "${PD_ETC}/app.env" <<'PYX'
import sys; p = sys.argv[1]; lines = open(p).read().splitlines()
open(p, "w").write("\n".join(l for l in lines if l != "MASTER_SECRET=live") + "\n")
PYX
export PS_SERVICES='garage\npostgres\n'
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "any other difference still fails" test "${RC}" -ne 0
check "says app.env differs" grep -q "app.env" <<<"${OUT}"

echo "drill_passes_on_a_night_from_before_the_rpc_secret"
# The first deploy of release 1 adds GARAGE_RPC_SECRET to app.env, so every older
# night lacks it; those nights must still drill clean (review, Important 3).
setup   # the night has no GARAGE_RPC_SECRET
echo "GARAGE_RPC_SECRET=$(printf 'a%.0s' $(seq 64))" >> "${PD_ETC}/app.env"
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "passes" test "${RC}" -eq 0
check "says DRILL PASSED" grep -q "DRILL PASSED" <<<"${OUT}"

echo "drill_passes_when_only_settings_that_hold_no_key_differ"
# A setting removed after the night was taken (the MINIO_* lines, 2026-11-19) or added
# since must not fail the drill of an older night; only the keys data is encrypted
# under decide it (MASTER_SECRET, OAUTH_TOKEN_SECRET).
setup   # the night has MASTER_SECRET=live and the minio address
printf "MINIO_ROOT_PASSWORD=dead\n" >> "${PD_ETC}/app.env"      # in the night, then removed live:
make_night 2026-11-18 "${NAS}/Backups/perfect-day/nightly/2026-11-18"
python3 "${HERE}/../lib/envfile.py" unset "${PD_ETC}/app.env" MINIO_ROOT_PASSWORD >/dev/null
echo "SOMETHING_NEW=1" >> "${PD_ETC}/app.env"
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "passes" test "${RC}" -eq 0
check "names the settings that differ" grep -q "MINIO_ROOT_PASSWORD" <<<"${OUT}"
check "and the new one" grep -q "SOMETHING_NEW" <<<"${OUT}"
check "never prints a value" bash -c '! grep -q "dead" <<<"$1"' _ "${OUT}"

echo "drill_fails_when_a_data_key_differs_or_is_missing"
setup
python3 "${HERE}/../lib/envfile.py" set "${PD_ETC}/app.env" OAUTH_TOKEN_SECRET live-oauth >/dev/null
make_night 2026-11-18 "${NAS}/Backups/perfect-day/nightly/2026-11-18"
python3 "${HERE}/../lib/envfile.py" set "${PD_ETC}/app.env" OAUTH_TOKEN_SECRET other-oauth >/dev/null
RC=0; OUT=$(bash "${DRILL}" 2>&1) || RC=$?
check "fails on a different OAUTH_TOKEN_SECRET" test "${RC}" -ne 0
check "names it" grep -q "OAUTH_TOKEN_SECRET" <<<"${OUT}"
check "never prints a value" bash -c '! grep -q "other-oauth\|live-oauth" <<<"$1"' _ "${OUT}"

echo "with_config_keeps_live_endpoint"
setup   # the night says minio
printf "MASTER_SECRET=changed\nS3_ENDPOINT_URL=http://garage:3900\n" > "${PD_ETC}/app.env"
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 --config-only <<<"replace config" 2>&1) || RC=$?
check "exits 0" test "${RC}" -eq 0
check "secrets are the backup's" grep -qx "MASTER_SECRET=live" "${PD_ETC}/app.env"
check "the live storage address is kept" grep -qx "S3_ENDPOINT_URL=http://garage:3900" "${PD_ETC}/app.env"
check "says so" grep -q "kept the storage address" <<<"${OUT}"

echo "config_only_rewrites_unknown_service_endpoint"
setup   # the night says minio
rm -f "${PD_ETC}/app.env"   # a new machine: no live config
printf "services:\n  postgres:\n    image: p\n  garage:\n    image: g\n" > "${PD_DEPLOY_DIR}/docker-compose.yml"
RC=0; OUT=$(bash "${RESTORE}" 2026-11-18 --config-only <<<"replace config" 2>&1) || RC=$?
check "exits 0" test "${RC}" -eq 0
check "points at Garage" grep -qx "S3_ENDPOINT_URL=http://garage:3900" "${PD_ETC}/app.env"
check "says why" grep -q "no minio service" <<<"${OUT}"
check "still no Docker" test ! -s "${CALLS}/docker"

echo "config_only_keeps_known_service_endpoint"
setup
rm -f "${PD_ETC}/app.env"
RC=0; bash "${RESTORE}" 2026-11-18 --config-only <<<"replace config" >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "minio kept while compose defines it" grep -qx "S3_ENDPOINT_URL=http://minio:9000" "${PD_ETC}/app.env"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "restore: all passed"; else echo "restore: ${FAILS} failed"; exit 1; fi
