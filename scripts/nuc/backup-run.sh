#!/usr/bin/env bash
# scripts/nuc/backup-run.sh — one nightly backup (tracker row 36).
# Run by perfect-day-backup.service (02:17 daily, installed by 30-backup.sh), or by
# hand: sudo systemctl start perfect-day-backup.service
#
# Dumps the database, exports the photo objects, saves /etc/perfect-day; encrypts
# all three with the backup key, checks them, keeps them under
# /var/backups/perfect-day/nightly/YYYY-MM-DD/ (30 nightly + 12 monthly) and copies
# new nights to the NAS, where the same keep rule is applied to all it holds.
# Result: /var/log/perfect-day/backup-status.json.
# Exit codes: 0 ok, 1 failed (nothing pruned or copied), 2 kept locally but the NAS
# copy failed, 75 another backup (or a deploy) holds the lock.
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
WORKDIR="${PD_NIGHTLY}/.${NIGHT}.partial"

fail() {
    backup_log "FAILED: $1"
    rm -rf "${WORKDIR}"
    backup_write_status failed "$1"
    exit 1
}

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

backup_prune_local "${NIGHT}"

if backup_sync_nas "${NIGHT}"; then
    NAS_OK=1
    backup_log "copied to ${PD_NAS_URL}"
    backup_write_status ok
    backup_log "done"
else
    rc=$?
    backup_log "NAS copy failed (rsync exit ${rc}); tonight's backup is kept locally"
    backup_write_status nas_failed "NAS copy failed (rsync exit ${rc})"
    exit 2
fi
