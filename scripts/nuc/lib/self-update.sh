# scripts/nuc/lib/self-update.sh — for a script that updates its own repository.
#
# Bash keeps running the copy of a script it started with, even after `git pull`
# replaces the file. So a fix to 40-update.sh would only take effect one deploy late
# (on 2026-11-17 the smoke-test cleanup was skipped for that reason). Call
# self_update_snapshot at the start, and self_update_rerun_if_changed "$@" right after
# the pull: if the file changed, the new copy runs from the beginning instead.
# The new copy runs the whole script again, so everything before the pull must be
# safe to repeat (the pull itself is then a no-op).

# Remembers which file is running and what it held when it started.
self_update_snapshot() {
    SELF_UPDATE_PATH="$(readlink -f "$0")"
    SELF_UPDATE_SUM="$(sha256sum "${SELF_UPDATE_PATH}" | cut -d' ' -f1)"
}

# Restarts the script with the new copy if the file changed since the snapshot.
# Restarts at most once: the restarted copy has SELF_UPDATE_RERUN set.
self_update_rerun_if_changed() {
    if [ -n "${SELF_UPDATE_RERUN:-}" ]; then
        return 0
    fi
    if [ "$(sha256sum "${SELF_UPDATE_PATH}" | cut -d' ' -f1)" != "${SELF_UPDATE_SUM}" ]; then
        echo "  $(basename "${SELF_UPDATE_PATH}") changed in this update; running the new copy"
        exec env SELF_UPDATE_RERUN=1 "${SELF_UPDATE_PATH}" "$@"
    fi
}
