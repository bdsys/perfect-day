#!/usr/bin/env bash
# scripts/nuc/backup-run.sh — one nightly backup (tracker row 36).
# Run by perfect-day-backup.service (03:00 Pacific daily, installed by 30-backup.sh), or by
# hand: sudo systemctl start perfect-day-backup.service
#
# Dumps the database, exports the photo objects, saves /etc/perfect-day; encrypts
# all three with the backup key, checks them, keeps them under
# /var/backups/perfect-day/nightly/YYYY-MM-DD/ (30 nightly + 12 monthly) and copies
# new nights to the NAS, where the same keep rule is applied to all it holds.
# Result: /var/log/perfect-day/backup-status.json.
# Exit codes: 0 ok, 1 failed (nothing pruned or copied), 2 kept locally but the NAS
# copy failed, 4 made and copied but retention skipped because the clock looks
# wrong (nothing deleted anywhere), 75 another backup (or a deploy) holds the lock.
set -euo pipefail
umask 077

# shellcheck source=lib/backup.sh
source "$(dirname "$(readlink -f "$0")")/lib/backup.sh"

mkdir -p "${PD_NIGHTLY}" "${PD_LOG_DIR}"
exec >> >(tee -a "${PD_LOG_DIR}/backup.log") 2>&1

exec 9>"${PD_LOCK}"
if ! flock -n 9; then
    echo "a backup or a deploy is already running; not starting another" >&2
    exit 75
fi

NIGHT="${TODAY:-$(date +%F)}"
LOCAL_OK=0
NAS_OK=0
FINISHED=0
RETENTION_SKIP=""
WORKDIR="${PD_NIGHTLY}/.${NIGHT}.partial"

fail() {
    backup_log "FAILED: $1"
    rm -rf "${WORKDIR}"
    backup_write_status failed "$1"
    FINISHED=1
    exit 1
}

# Any other way out (an unexpected error, systemd's 15-minute cap, Ctrl-C) still
# leaves a status that says the run did not finish, never yesterday's "ok".
on_exit() {
    local rc=$?
    [ "${FINISHED}" = 1 ] && return
    # When systemd stops the whole unit the log tee is gone too, so write the
    # status before any output, and don't let a broken pipe end this handler.
    trap '' PIPE
    set +e
    backup_write_status failed "stopped before finishing (exit ${rc})"
    rm -rf "${WORKDIR}"
    backup_log "FAILED: stopped before finishing (exit ${rc})" 2>/dev/null
}
trap on_exit EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

backup_log "starting backup ${NIGHT}"

free_kb="$(df -Pk "${PD_BACKUP_ROOT}" | awk 'NR==2 {print $4}')"
if [ "${free_kb}" -lt "${PD_MIN_FREE_KB}" ]; then
    fail "not enough free disk on ${PD_BACKUP_ROOT}: ${free_kb} KB, need ${PD_MIN_FREE_KB} KB"
fi

rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}"
err="$(mktemp)"
backup_make_night "${WORKDIR}" 2>"${err}" || fail "$(tail -n 1 "${err}")"
backup_verify_night "${WORKDIR}" 2>"${err}" || fail "$(tail -n 1 "${err}")"
rm -f "${err}"

# Swap the verified night into place; a same-day rerun replaces the earlier one.
FINAL="${PD_NIGHTLY}/${NIGHT}"
if [ -e "${FINAL}" ]; then
    mv "${FINAL}" "${PD_NIGHTLY}/.${NIGHT}.old"
fi
mv "${WORKDIR}" "${FINAL}"
rm -rf "${PD_NIGHTLY}/.${NIGHT}.old"
LOCAL_OK=1
backup_log "night ${NIGHT} written and verified"

# Retention only with a clock we can trust; otherwise keep everything, here and on the NAS.
SYNC_MODE=""
# shellcheck disable=SC2046  # word splitting of folder names is intended
if why="$(backup_clock_check "${NIGHT}" $(ls -A "${PD_NIGHTLY}"))"; then
    backup_prune_local "${NIGHT}"
else
    RETENTION_SKIP="${why}"
    SYNC_MODE="--no-delete"
fi

rc=0
backup_sync_nas "${NIGHT}" ${SYNC_MODE} || rc=$?
if [ "${rc}" -ne 0 ]; then
    backup_log "NAS copy failed (rsync exit ${rc}); tonight's backup is kept locally"
    backup_write_status nas_failed "NAS copy failed (rsync exit ${rc})"
    FINISHED=1
    exit 2
fi
NAS_OK=1
backup_log "copied to ${PD_NAS_URL}"
if [ -n "${RETENTION_SKIP}" ]; then
    backup_log "WARNING: retention skipped, nothing deleted: ${RETENTION_SKIP}. Check the date and time."
    backup_write_status warning "retention skipped (clock looks wrong): ${RETENTION_SKIP}"
    FINISHED=1
    exit 4
fi
backup_write_status ok
FINISHED=1
backup_log "done"
