# scripts/nuc/lib/deploy-lock.sh — the lock shared by deploys and the nightly backup.
# 40-update.sh never deploys while scripts/nuc/backup-run.sh runs (its photo export
# uses a one-off API container and its dump reads Postgres), and never runs twice.

# deploy_lock_take: takes the lock on fd 8, waiting up to PD_LOCK_WAIT seconds
# (default 1800). Returns non-zero if it could not.
#
# A copy restarted by self_update_rerun_if_changed (lib/self-update.sh) inherits
# fd 8 with the lock already held, so it keeps that and does not open the file
# again: reopening would wait on the first copy's lock, still held by its log tee.
deploy_lock_take() {
    if [ -n "${SELF_UPDATE_RERUN:-}" ] && { true >&8; } 2>/dev/null; then
        return 0
    fi
    exec 8>"${PD_LOCK:-/run/perfect-day-backup.lock}"
    flock -w "${PD_LOCK_WAIT:-1800}" 8
}
