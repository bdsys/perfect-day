#!/usr/bin/env bash
# Tests for the nightly backup (scripts/nuc/backup-run.sh, lib/backup.sh), tracker row 36.
# docker, rsync, age and df are PATH shims that log their arguments, so this runs
# anywhere with bash, tar, gzip, jq and flock, and never touches /var, /etc or Docker.
# Usage: bash scripts/nuc/tests/backup.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${HERE}/../backup-run.sh"
LIB="${HERE}/../lib/backup.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

# A fresh world: shims on PATH, fake /etc/perfect-day, empty backup root.
setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    mkdir -p "${WORK}/bin" "${WORK}/etc/perfect-day" "${WORK}/backups" "${WORK}/log" \
        "${WORK}/deploy" "${WORK}/objects" "${WORK}/calls"
    echo "MASTER_SECRET=test" > "${WORK}/etc/perfect-day/app.env"
    echo "PUBLIC" > "${WORK}/etc/perfect-day/backup.pub"
    echo "PRIVATE" > "${WORK}/etc/perfect-day/backup.key"
    printf 'one' > "${WORK}/objects/a.bin"
    printf 'two!' > "${WORK}/objects/b.bin"

    # age: "encrypts" and "decrypts" as cat; logs whether it was -R or -d.
    cat > "${WORK}/bin/age" <<'EOF'
#!/usr/bin/env bash
echo "age $*" >> "${CALLS}/age"
if [ "$1" = "-d" ]; then
    n=$(( $(cat "${CALLS}/age.n" 2>/dev/null || echo 0) + 1 )); echo "${n}" > "${CALLS}/age.n"
    cat
    [ "${AGE_FAIL_NTH:-0}" = "${n}" ] && exit 1
    exit 0
fi
cat
EOF
    # docker: pg_dump, psql row counts and photos export, each failable by env.
    cat > "${WORK}/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${CALLS}/docker"
args="$*"
case "${args}" in
    *pg_dump*)
        [ -n "${FAIL_PGDUMP:-}" ] && { echo "pg_dump: connection refused" >&2; exit 1; }
        [ -n "${SLEEP_PGDUMP:-}" ] && sleep "${SLEEP_PGDUMP}"
        printf -- '-- dump\nCREATE TABLE t();\n'
        [ -z "${TRUNCATED_DUMP:-}" ] && printf -- '--\n-- PostgreSQL database dump complete\n--\n\n\\unrestrict abc\n'
        exit 0 ;;
    *psql*) echo "1|2|3|4"; exit 0 ;;
    *"photos export"*)
        echo "Container perfect-day-api-run Creating" >&2
        tar -C "${OBJECTS}" -cf - a.bin b.bin
        echo "{\"objects\": ${EXPORT_COUNT:-2}, \"bytes\": 7}" >&2
        exit 0 ;;
esac
exit 0
EOF
    # rsync: logs its args; RSYNC_EXIT makes it fail like an unreachable NAS.
    cat > "${WORK}/bin/rsync" <<'EOF'
#!/usr/bin/env bash
echo "rsync $*" >> "${CALLS}/rsync"
[ "${RSYNC_EXIT:-0}" != 0 ] && exit "${RSYNC_EXIT}"
if [[ " $* " == *" --list-only "* ]]; then
    echo "drwx------ 4096 2026/11/18 02:17:00 ."
    for n in ${NAS_LIST:-}; do echo "drwx------ 4096 2026/11/18 02:17:00 ${n}"; done
fi
exit 0
EOF
    # df -Pk: reports FREE_KB available.
    cat > "${WORK}/bin/df" <<'EOF'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "/dev/x 100000000 1 ${FREE_KB:-50000000} 1% /"
EOF
    chmod +x "${WORK}/bin/"*

    export PATH="${WORK}/bin:${PATH}" CALLS="${WORK}/calls" OBJECTS="${WORK}/objects"
    export PD_BACKUP_ROOT="${WORK}/backups" PD_ETC="${WORK}/etc/perfect-day" \
        PD_LOG_DIR="${WORK}/log" PD_DEPLOY_DIR="${WORK}/deploy" \
        PD_NAS_URL="rsync://nas.invalid/Backups/perfect-day/nightly/" \
        PD_LOCK="${WORK}/backup.lock" TODAY="2026-11-18"
    unset FAIL_PGDUMP TRUNCATED_DUMP EXPORT_COUNT RSYNC_EXIT FREE_KB NAS_LIST AGE_FAIL_NTH SLEEP_PGDUMP
    touch "${CALLS}/docker" "${CALLS}/rsync" "${CALLS}/age"
}

status() { jq -r "$1" "${PD_LOG_DIR}/backup-status.json"; }
old_nights() { # old_nights <name...>: good-looking nights from earlier runs
    local n; for n in "$@"; do mkdir -p "${PD_BACKUP_ROOT}/nightly/${n}"; echo old > "${PD_BACKUP_ROOT}/nightly/${n}/manifest.json"; done
}

# ── retention ────────────────────────────────────────────────────────────────
echo "retention: 400 days of nightly folders"
setup
# shellcheck source=../lib/backup.sh
source "${LIB}"
NAMES=()
d="2025-10-15"
while [[ "${d}" < "2026-11-19" ]]; do NAMES+=("${d}"); d=$(date -d "${d} +1 day" +%F); done
KEPT="$(backup_keep_list 2026-11-18 "${NAMES[@]}")"
EXPECTED="$( { for m in 2025-12 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09 2026-10; do echo "${m}-01"; done
    d="2026-10-20"; while [[ "${d}" < "2026-11-19" ]]; do echo "${d}"; d=$(date -d "${d} +1 day" +%F); done; } | sort)"
check "keeps the newest 30 plus the first of each of the 12 months (41 folders)" test "${KEPT}" = "${EXPECTED}"
check "41 folders kept" test "$(wc -l <<<"${KEPT}")" -eq 41

echo "retention: first run"
check "a single folder is kept" test "$(backup_keep_list 2026-11-18 2026-11-18)" = "2026-11-18"
check "ignores names that are not dates" test "$(backup_keep_list 2026-11-18 2026-11-18 .2026-11-17.partial notes)" = "2026-11-18"

# ── a good run ───────────────────────────────────────────────────────────────
echo "run: a good night"
setup
old_nights 2026-11-16 2026-11-17
RC=0; OUT=$(bash "${RUN}" 2>&1) || RC=$?
NIGHT="${PD_BACKUP_ROOT}/nightly/2026-11-18"
check "exits 0" test "${RC}" -eq 0
check "writes the three encrypted files and the manifest" \
    test -f "${NIGHT}/db.sql.gz.age" -a -f "${NIGHT}/photos.tar.age" -a -f "${NIGHT}/config.tar.age" -a -f "${NIGHT}/manifest.json"
check "leaves no .partial behind" test -z "$(find "${PD_BACKUP_ROOT}" -name '*.partial*')"
check "encrypts with the public key" grep -q -- "-R ${PD_ETC}/backup.pub" "${CALLS}/age"
check "manifest counts the objects" test "$(jq -r .objects "${NIGHT}/manifest.json")" -eq 2
check "manifest has row counts" test "$(jq -r .rows.photos "${NIGHT}/manifest.json")" -eq 4
check "manifest has a sha256 per file" test "$(jq -r '.files["db.sql.gz.age"].sha256' "${NIGHT}/manifest.json")" = "$(sha256sum "${NIGHT}/db.sql.gz.age" | cut -d' ' -f1)"
check "copies nightly/ to the NAS" grep -q -- "-a --timeout=60 ${PD_BACKUP_ROOT}/nightly/ ${PD_NAS_URL}" "${CALLS}/rsync"
check "the copy itself never deletes" bash -c '! grep -- "${1}/nightly/ " "$2" | grep -q -- "--delete"' _ "${PD_BACKUP_ROOT}" "${CALLS}/rsync"
check "an empty NAS needs no deleting" bash -c '! grep -q -- "--delete" "$1"' _ "${CALLS}/rsync"
check "rsync has a timeout" grep -q -- "--timeout=60" "${CALLS}/rsync"
check "status_json_fields: every field is there" \
    bash -c 'jq -e "has(\"last_attempt\") and has(\"last_local_success\") and has(\"last_nas_success\") and has(\"result\") and has(\"error\") and has(\"night\") and has(\"sizes\")" "$1" >/dev/null' _ "${PD_LOG_DIR}/backup-status.json"
check "status says ok" test "$(status .result)" = "ok"
check "status night" test "$(status .night)" = "2026-11-18"
check "logs to backup.log" grep -q "2026-11-18" "${PD_LOG_DIR}/backup.log"
check "files are private (umask 077)" test "$(stat -c %a "${NIGHT}/db.sql.gz.age")" = "600"

echo "config backup"
TAR_LIST="$(tar -tf "${NIGHT}/config.tar.age")"
check "contains app.env" grep -qx "${PD_ETC#/}/app.env" <<<"${TAR_LIST}"
check "config_tar_excludes_backup_key" bash -c '! grep -q "backup.key" <<<"$1"' _ "${TAR_LIST}"

# ── verify rejects bad nights ───────────────────────────────────────────────
echo "verify_rejects_truncated_dump"
setup; old_nights 2026-11-17
export TRUNCATED_DUMP=1
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "no night folder for today" test ! -e "${PD_BACKUP_ROOT}/nightly/2026-11-18"
check "status failed and says why" bash -c '[ "$(jq -r .result "$1")" = failed ] && jq -r .error "$1" | grep -qi dump' _ "${PD_LOG_DIR}/backup-status.json"

echo "verify_rejects_object_count_mismatch"
setup; export EXPORT_COUNT=3
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "no night folder" test ! -e "${PD_BACKUP_ROOT}/nightly/2026-11-18"
check "says the counts differ" bash -c 'jq -r .error "$1" | grep -q "has 2 objects, the export reported 3"' _ "${PD_LOG_DIR}/backup-status.json"

echo "verify_rejects_config_without_app_env"
setup; rm "${PD_ETC}/app.env"
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "says app.env is missing" bash -c 'jq -r .error "$1" | grep -q "no app.env"' _ "${PD_LOG_DIR}/backup-status.json"

# ── failures never delete ───────────────────────────────────────────────────
echo "failed_run_prunes_nothing"
setup
for i in $(seq 1 40); do old_nights "$(date -d "2026-11-18 -${i} day" +%F)"; done
BEFORE=$(ls "${PD_BACKUP_ROOT}/nightly" | wc -l)
export FAIL_PGDUMP=1
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "every old folder is still there" test "$(ls "${PD_BACKUP_ROOT}/nightly" | wc -l)" -eq "${BEFORE}"
check "rsync never ran" test ! -s "${CALLS}/rsync"
check "result failed" test "$(status .result)" = "failed"

echo "nas_unreachable_keeps_local_and_fails"
setup
echo '{"last_nas_success":"2026-11-17T02:17:00Z"}' > "${PD_LOG_DIR}/backup-status.json"
old_nights 2026-11-17
export RSYNC_EXIT=10
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 2" test "${RC}" -eq 2
check "tonight's folder is kept" test -f "${PD_BACKUP_ROOT}/nightly/2026-11-18/manifest.json"
check "result nas_failed" test "$(status .result)" = "nas_failed"
check "last_local_success is set" test "$(status .last_local_success)" != "null"
check "last_nas_success unchanged" test "$(status .last_nas_success)" = "2026-11-17T02:17:00Z"

echo "rebuilt_machine_keeps_nas_history"
setup
# The NAS holds 60 nights from the old machine; the rebuilt one has only 2.
d="2026-09-18"; NAS_LIST=""
while [[ "${d}" < "2026-11-17" ]]; do NAS_LIST="${NAS_LIST} ${d}"; d=$(date -d "${d} +1 day" +%F); done
export NAS_LIST
old_nights 2026-11-17
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
DEL="$(grep -- "--delete" "${CALLS}/rsync" || true)"
check "exits 0" test "${RC}" -eq 0
check "deletes an old nightly the rule drops (2026-09-19)" grep -qF -- "--include=/2026-09-19/***" <<<"${DEL}"
check "keeps September's monthly (2026-09-18)" bash -c '! grep -q -- "/2026-09-18/" <<<"$1"' _ "${DEL}"
check "keeps October's monthly (2026-10-01)" bash -c '! grep -q -- "/2026-10-01/" <<<"$1"' _ "${DEL}"
check "keeps the newest NAS nights (2026-11-16)" bash -c '! grep -q -- "/2026-11-16/" <<<"$1"' _ "${DEL}"
check "the delete is limited to named folders" grep -qF -- "--exclude=*" <<<"${DEL}"
check "every --delete call names its folders" bash -c '! grep -- "--delete" "$1" | grep -qv -- "--include="' _ "${CALLS}/rsync"

echo "tiny_local_set_never_deletes_on_nas"
setup
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "first run exits 0" test "${RC}" -eq 0
check "rsync ran" test -s "${CALLS}/rsync"
check "without --delete" bash -c '! grep -q -- "--delete" "$1"' _ "${CALLS}/rsync"

echo "NAS listing fails: nothing is deleted"
setup; old_nights 2026-11-17
printf '#!/usr/bin/env bash\necho "rsync $*" >> "${CALLS}/rsync"\n[[ " $* " == *" --list-only "* ]] && exit 10\nexit 0\n' > "${WORK}/bin/rsync"
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 2" test "${RC}" -eq 2
check "no delete attempted" bash -c '! grep -q -- "--delete" "$1"' _ "${CALLS}/rsync"

# ── concurrency, reruns, disk ───────────────────────────────────────────────
echo "lock_held_exits_75"
setup
exec 7>"${PD_LOCK}"; flock -n 7
RC=0; OUT=$(bash "${RUN}" 2>&1) || RC=$?
exec 7>&-
check "exits 75" test "${RC}" -eq 75
check "says already running" grep -qi "already running" <<<"${OUT}"
check "did nothing" test ! -s "${CALLS}/docker"

echo "same_day_rerun_replaces_atomically"
setup
old_nights 2026-11-17 2026-11-18
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "today's folder is the new one" test -f "${PD_BACKUP_ROOT}/nightly/2026-11-18/db.sql.gz.age"
check "the old manifest was replaced" bash -c '! grep -qx old "$1"' _ "${PD_BACKUP_ROOT}/nightly/2026-11-18/manifest.json"
check "nothing half-made is left" test -z "$(find "${PD_BACKUP_ROOT}/nightly" -maxdepth 1 -name '.*')"

echo "low_disk_refuses"
setup; export FREE_KB=1048576
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "never dumped" bash -c '! grep -q pg_dump "$1"' _ "${CALLS}/docker"
check "error mentions disk" bash -c 'jq -r .error "$1" | grep -qi disk' _ "${PD_LOG_DIR}/backup-status.json"

echo "nas_parent_created_first"
setup
bash "${RUN}" >/dev/null 2>&1
check "makes Backups/perfect-day/ on the NAS before copying" \
    bash -c 'head -n 1 "$1" | grep -q -- " rsync://nas.invalid/Backups/perfect-day/$"' _ "${CALLS}/rsync"

echo "photos_decrypt_failure_detected"
setup
export AGE_FAIL_NTH=3   # verify decrypts db twice (key check, marker), then photos
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 1" test "${RC}" -eq 1
check "no night kept" test ! -e "${PD_BACKUP_ROOT}/nightly/2026-11-18"
check "says the photo backup is unreadable" bash -c 'jq -r .error "$1" | grep -qi "photo backup"' _ "${PD_LOG_DIR}/backup-status.json"

echo "term_writes_failed_status"
setup
echo '{"result":"ok","last_attempt":"2026-11-17T02:17:00Z"}' > "${PD_LOG_DIR}/backup-status.json"
export SLEEP_PGDUMP=5
setsid bash "${RUN}" >/dev/null 2>&1 &
PID=$!
sleep 1
kill -TERM -- "-${PID}" 2>/dev/null || true
wait "${PID}" 2>/dev/null || true
check "status no longer says ok" test "$(status .result)" = "failed"
check "says it was stopped" bash -c 'jq -r .error "$1" | grep -qi "stopped"' _ "${PD_LOG_DIR}/backup-status.json"
check "no half-made night left" test -z "$(find "${PD_BACKUP_ROOT}/nightly" -mindepth 1 -maxdepth 1)"

echo "clock_in_past_keeps_everything"
setup
for i in $(seq 1 40); do old_nights "$(date -d "2026-11-18 -${i} day" +%F)"; done
export NAS_LIST="2025-12-01 2026-01-01 2026-06-01"
export TODAY="2026-03-01"
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 4 (warning)" test "${RC}" -eq 4
check "every old night is kept" test "$(find "${PD_BACKUP_ROOT}/nightly" -mindepth 1 -maxdepth 1 -name '2026-1*' | wc -l)" -eq 40
check "tonight's backup is kept" test -f "${PD_BACKUP_ROOT}/nightly/2026-03-01/manifest.json"
check "still copied to the NAS" grep -q -- "nightly/ rsync://nas.invalid" "${CALLS}/rsync"
check "nothing deleted on the NAS" bash -c '! grep -q -- "--delete" "$1"' _ "${CALLS}/rsync"
check "result warning, says the clock" bash -c '[ "$(jq -r .result "$1")" = warning ] && jq -r .error "$1" | grep -qi clock' _ "${PD_LOG_DIR}/backup-status.json"

echo "clock_far_future_keeps_everything"
setup
for i in $(seq 1 40); do old_nights "$(date -d "2026-11-18 -${i} day" +%F)"; done
export TODAY="2027-06-01"
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 4 (warning)" test "${RC}" -eq 4
check "every old night is kept" test "$(find "${PD_BACKUP_ROOT}/nightly" -mindepth 1 -maxdepth 1 -name '2026-1*' | wc -l)" -eq 40

echo "ten_day_gap_still_prunes"
setup
for i in $(seq 10 55); do old_nights "$(date -d "2026-11-18 -${i} day" +%F)"; done
RC=0; bash "${RUN}" >/dev/null 2>&1 || RC=$?
check "exits 0" test "${RC}" -eq 0
check "an old non-monthly night is pruned (2026-10-02)" test ! -e "${PD_BACKUP_ROOT}/nightly/2026-10-02"

echo "retention applied after a good run"
setup
for i in $(seq 1 45); do old_nights "$(date -d "2026-11-18 -${i} day" +%F)"; done
bash "${RUN}" >/dev/null 2>&1
check "the oldest non-monthly folder is gone" test ! -e "${PD_BACKUP_ROOT}/nightly/2026-10-05"
check "the earliest October folder stays as the monthly" test -d "${PD_BACKUP_ROOT}/nightly/2026-10-04"
check "30 newest kept" test -d "${PD_BACKUP_ROOT}/nightly/2026-10-20"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "backup: all passed"; else echo "backup: ${FAILS} failed"; exit 1; fi
