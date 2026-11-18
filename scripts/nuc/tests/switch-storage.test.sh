#!/usr/bin/env bash
# Tests for scripts/nuc/60-switch-storage.sh, the one-time MinIO <-> Garage switch
# (tracker row 41). docker, df and the smoke test are PATH shims or stubs that log
# what they were asked to do; the env file is a temp file behind a symlink, as on
# the NUC. Never touches /etc, /var, /opt or Docker.
# Usage: bash scripts/nuc/tests/switch-storage.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWITCH="${HERE}/../60-switch-storage.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

MINIO_PW="minio-root-password-48-characters-xxxxxxxxxxxxxx"
S3_PW="s3-secret-key-value-for-the-app-yyyyyyyyyyyyyyyy"

setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    mkdir -p "${WORK}/bin" "${WORK}/etc" "${WORK}/deploy/scripts" "${WORK}/backups" "${WORK}/calls"
    cat > "${WORK}/etc/app.env" <<EOF
MASTER_SECRET=live-master
MINIO_ROOT_USER=perfectday
MINIO_ROOT_PASSWORD=${MINIO_PW}
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=perfectday
S3_SECRET_KEY=${S3_PW}
S3_BUCKET_PHOTOS=photos
EOF
    chmod 640 "${WORK}/etc/app.env"
    cp "${WORK}/etc/app.env" "${WORK}/orig.env"
    ln -s "${WORK}/etc/app.env" "${WORK}/deploy/.env"
    printf '#!/usr/bin/env bash\necho smoke >> "%s/calls/order"\nexit "${SMOKE_EXIT:-0}"\n' "${WORK}" \
        > "${WORK}/deploy/scripts/smoke-test.sh"
    printf '#!/usr/bin/env bash\necho backup >> "%s/calls/order"\nexit "${BACKUP_EXIT:-0}"\n' "${WORK}" \
        > "${WORK}/backup-stub.sh"
    chmod +x "${WORK}/deploy/scripts/smoke-test.sh" "${WORK}/backup-stub.sh"

    # docker: logs every call (args only) and a one-word step to calls/order. Which
    # store a one-off api run talks to comes from S3_ENDPOINT_URL in its environment,
    # exactly as the real script hands it over (-e NAME, value from the environment).
    cat > "${WORK}/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${CALLS}/docker"
a="$*"
step() { echo "$1" >> "${CALLS}/order"; }
store() { case "${S3_ENDPOINT_URL:-}" in *minio*) echo minio ;; *garage*) echo garage ;; *) echo live ;; esac; }
case "${a}" in
    "info -f"*) echo "${WORK}/docker-root"; exit 0 ;;
    *"up -d --wait"*) step up-wait; exit "${UP_FAIL:-0}" ;;
    *"photos missing"*) step "missing-$(store)"; echo "missing=${MISSING:-0}"; [ "${MISSING:-0}" = 0 ]; exit $? ;;
    *"photos digest"*)
        s="$(store)"; step "digest-${s}"
        if [ "${s}" = "${DST_STORE:-garage}" ] && [ -n "${DST_DIGEST:-}" ]; then echo "${DST_DIGEST}"
        else echo '{"objects": 3, "bytes": 1000, "digest": "abc"}'; fi
        exit 0 ;;
    *"photos export"*) step "export-$(store)"; printf 'TARBYTES'; exit 0 ;;
    *"photos import --exact"*)
        step "import-$(store)"; cat > /dev/null
        if [ -n "${TERM_DURING_IMPORT:-}" ]; then kill -TERM "$(cat "${WORK}/switch.pid")"; sleep 0.3; fi
        echo '{"objects": 3, "bytes": 1000, "deleted": 0}' >&2; exit 0 ;;
    *"photos verify"*)
        s="$(store)"; step "verify-${s}"; echo "checked=3 failures=0"
        if [ "${s}" = live ]; then exit "${VERIFY_LIVE_EXIT:-0}"; fi
        exit "${VERIFY_DST_EXIT:-0}" ;;
    *pg_dump*) step dump; printf -- '-- dump\n'; exit "${DUMP_FAIL:-0}" ;;
    *readyz*) step readyz; echo "${READYZ:-ok}"; exit 0 ;;
    *" stop api worker beat"*) step stop-app; exit 0 ;;
    *" up -d --no-deps api worker beat"*) step start-app; exit 0 ;;
    *" stop minio"*) step stop-minio; exit 0 ;;
    *" stop garage"*) step stop-garage; exit 0 ;;
esac
exit 0
EOF
    printf '#!/usr/bin/env bash\necho "Filesystem 1K-blocks Used Available Use%% Mounted"\necho "x 1 1 ${DF_AVAIL_KB:-99999999} 1%% /"\n' \
        > "${WORK}/bin/df"
    chmod +x "${WORK}/bin/"*
    mkdir -p "${WORK}/docker-root"
    export PATH="${WORK}/bin:${PATH}" CALLS="${WORK}/calls" WORK
    export PD_DEPLOY_DIR="${WORK}/deploy" PD_ETC="${WORK}/etc" PD_BACKUP_ROOT="${WORK}/backups" \
        PD_LOCK="${WORK}/lock" PD_SMOKE_URL="http://smoke.test" PD_BACKUP_CMD="${WORK}/backup-stub.sh" \
        PD_RUN_BACKUP=1 PD_NOW_HHMM=1200 PD_COMPOSE_PROJECT="" PD_COMPOSE_PROFILE="nuc" \
        PD_READY_TIMEOUT=2
    unset UP_FAIL MISSING DST_DIGEST VERIFY_DST_EXIT VERIFY_LIVE_EXIT READYZ SMOKE_EXIT BACKUP_EXIT \
        DF_AVAIL_KB TERM_DURING_IMPORT DUMP_FAIL 2>/dev/null || true
    touch "${CALLS}/order" "${CALLS}/docker"
}

run_switch() { # run_switch [args...]; sets RC and OUT
    RC=0
    OUT="$(bash "${SWITCH}" "$@" 2>&1)" || RC=$?
}
# Export and import run at once in a pipe, so their two lines may land either way round.
order() { tr '\n' ' ' < "${CALLS}/order" | sed -E 's/ $//; s/(import-[a-z]+) (export-[a-z]+)/\2 \1/'; }
endpoint() { sed -n 's/^S3_ENDPOINT_URL=//p' "${WORK}/etc/app.env"; }

echo "forward_happy_path"
setup
run_switch
check "exits 0" test "${RC}" -eq 0
check "steps in order" test "$(order)" = \
    "up-wait missing-minio digest-minio dump stop-app export-minio import-garage digest-minio digest-garage verify-garage start-app readyz verify-live smoke stop-minio backup"
check "endpoint is Garage" test "$(endpoint)" = "http://garage:3900"
check "env file is still behind its symlink" test -L "${WORK}/deploy/.env"
check "env file mode kept" test "$(stat -c %a "${WORK}/etc/app.env")" = "640"
check "dump is private" bash -c 'f=$(ls "$1"/pre-switch-*.sql.gz) && test "$(stat -c %a "$f")" = 600' _ "${WORK}/backups"
check "summary names the way back" grep -q "60-switch-storage.sh --back" <<<"${OUT}"
check "Garage RPC secret ensured" grep -q '^GARAGE_RPC_SECRET=' "${WORK}/etc/app.env"

echo "back_happy_path"
setup
python3 "${HERE}/../lib/envfile.py" set "${WORK}/etc/app.env" S3_ENDPOINT_URL http://garage:3900 >/dev/null
run_switch --back
check "exits 0" test "${RC}" -eq 0
check "reads Garage, writes MinIO" grep -q "export-garage import-minio" <<<"$(order)"
check "completeness checked on Garage" grep -q "missing-garage" <<<"$(order)"
check "endpoint is MinIO" test "$(endpoint)" = "http://minio:9000"
check "stops Garage afterwards" grep -q "stop-garage" <<<"$(order)"
check "summary names the way forward" grep -q "60-switch-storage.sh$\|60-switch-storage.sh " <<<"${OUT}"

echo "already_there_refuses"
setup
python3 "${HERE}/../lib/envfile.py" set "${WORK}/etc/app.env" S3_ENDPOINT_URL http://garage:3900 >/dev/null
run_switch
check "exits 2" test "${RC}" -eq 2
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"

echo "unknown_endpoint_refuses"
setup
python3 "${HERE}/../lib/envfile.py" set "${WORK}/etc/app.env" S3_ENDPOINT_URL https://s3.example.com >/dev/null
run_switch
check "exits 2" test "${RC}" -eq 2
check "says what it found" grep -q "s3.example.com" <<<"${OUT}"

echo "source_missing_objects_refuses_before_any_change"
setup
export MISSING=3
run_switch
check "exits 2" test "${RC}" -eq 2
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"
check "nothing imported" bash -c '! grep -q import "$1"' _ "${CALLS}/order"
check "says how many" grep -q "missing=3" <<<"${OUT}"
check "env file unchanged apart from the RPC secret" \
    bash -c 'diff <(grep -v ^GARAGE_RPC_SECRET= "$1") "$2" >/dev/null' _ "${WORK}/etc/app.env" "${WORK}/orig.env"
check "endpoint still MinIO" test "$(endpoint)" = "http://minio:9000"

echo "destination_unhealthy_changes_nothing"
setup
export UP_FAIL=1
run_switch
check "exits 2" test "${RC}" -eq 2
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"
check "endpoint still MinIO" test "$(endpoint)" = "http://minio:9000"
check "says the store did not become healthy" grep -qi "healthy" <<<"${OUT}"

echo "low_disk_refuses"
setup
export DF_AVAIL_KB=1   # 1 KB free; the source holds 1000 bytes, so 2000 are needed
run_switch
check "exits 2" test "${RC}" -eq 2
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"
check "says disk space" grep -qi "space" <<<"${OUT}"

echo "dump_failure_refuses"
setup
export DUMP_FAIL=1
run_switch
check "exits 2" test "${RC}" -eq 2
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"
check "says the dump failed" grep -q "could not dump" <<<"${OUT}"

echo "digest_mismatch_restarts_on_source"
setup
export DST_DIGEST='{"objects": 2, "bytes": 900, "digest": "zzz"}'
run_switch
check "exits 1" test "${RC}" -eq 1
check "endpoint still MinIO" test "$(endpoint)" = "http://minio:9000"
check "app started again" test "$(tail -n 1 "${CALLS}/order")" = "start-app"
check "source not stopped" bash -c '! grep -q stop-minio "$1"' _ "${CALLS}/order"
check "shows both counts" grep -q "3 objects" <<<"${OUT}"

echo "destination_verify_failure_restarts_on_source"
setup
export VERIFY_DST_EXIT=1
run_switch
check "exits 1" test "${RC}" -eq 1
check "endpoint still MinIO" test "$(endpoint)" = "http://minio:9000"
check "app started again" test "$(tail -n 1 "${CALLS}/order")" = "start-app"

echo "readyz_storage_error_flips_back"
setup
export READYZ=error
run_switch
check "exits 1" test "${RC}" -eq 1
check "endpoint back to MinIO" test "$(endpoint)" = "http://minio:9000"
check "app restarted after flipping back" test "$(tail -n 1 "${CALLS}/order")" = "start-app"
check "source not stopped" bash -c '! grep -q stop-minio "$1"' _ "${CALLS}/order"
check "no backup run" bash -c '! grep -q backup "$1"' _ "${CALLS}/order"

echo "smoke_failure_flips_back"
setup
export SMOKE_EXIT=1
run_switch
check "exits 1" test "${RC}" -eq 1
check "endpoint back to MinIO" test "$(endpoint)" = "http://minio:9000"

echo "trap_restores_source_on_term"
setup
export TERM_DURING_IMPORT=1
RC=0
bash "${SWITCH}" > "${WORK}/out" 2>&1 &
echo $! > "${WORK}/switch.pid"
wait $! || RC=$?
check "exits non-zero" test "${RC}" -ne 0
check "endpoint still MinIO" test "$(endpoint)" = "http://minio:9000"
check "app started again" test "$(tail -n 1 "${CALLS}/order")" = "start-app"

echo "near_backup_window_refuses_without_flag"
setup
export PD_NOW_HHMM=0205
run_switch
check "exits 2" test "${RC}" -eq 2
check "says why" grep -q "02:17" <<<"${OUT}"
check "nothing stopped" bash -c '! grep -q stop "$1"' _ "${CALLS}/order"
setup
export PD_NOW_HHMM=0240
run_switch
check "02:40 is also too close" test "${RC}" -eq 2
setup
export PD_NOW_HHMM=0205
run_switch --yes-near-backup
check "proceeds with the flag" test "${RC}" -eq 0

echo "lock_held_exits_75"
setup
exec 7>"${PD_LOCK}"; flock -n 7
run_switch
check "exits 75" test "${RC}" -eq 75
check "nothing done" test ! -s "${CALLS}/order"
exec 7>&-

echo "backup_failure_does_not_undo_the_switch"
setup
export BACKUP_EXIT=1
run_switch
check "exits 0" test "${RC}" -eq 0
check "endpoint is Garage" test "$(endpoint)" = "http://garage:3900"
check "says the backup failed" grep -qi "backup.*fail\|fail.*backup" <<<"${OUT}"

echo "never_prints_secrets"
setup
run_switch
check "MinIO password not printed" bash -c '! grep -q "$1" <<<"$2"' _ "${MINIO_PW}" "${OUT}"
check "S3 secret not printed" bash -c '! grep -q "$1" <<<"$2"' _ "${S3_PW}" "${OUT}"
check "no secret on a docker command line" \
    bash -c '! grep -q -e "$1" -e "$2" "$3"' _ "${MINIO_PW}" "${S3_PW}" "${CALLS}/docker"

echo "never_removes"
check "no rm, down, rmi, prune or volume calls" \
    bash -c '! grep -qE " (rm|down|rmi|prune)( |$)|volume |image " <(grep -v -- "run --rm" "$1")' _ "${CALLS}/docker"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "switch-storage: all passed"; else echo "switch-storage: ${FAILS} failed"; exit 1; fi
