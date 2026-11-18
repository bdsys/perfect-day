# scripts/nuc/lib/readyz.sh — "is the app really ready?" for deploys (tracker row 41).
#
# /readyz answers 200 even when photo storage is down (storage is warning-level, so
# a store problem never takes the API off the network). A deploy must not call that
# healthy: after the switch to Garage, a store that fails to start (for example
# because the secret in app.env changed) would otherwise go unnoticed until the
# nightly backup failed.

# readyz_storage_ok <readyz url>: 0 if it answers and checks.storage is "ok".
readyz_storage_ok() {
    local body storage
    body="$(curl -s --max-time 10 "$1")" || { echo "readyz: no answer from $1" >&2; return 1; }
    storage="$(jq -r '.checks.storage // "missing"' <<<"${body}" 2>/dev/null)" \
        || { echo "readyz: $1 did not answer with JSON" >&2; return 1; }
    [ "${storage}" = ok ] && return 0
    echo "readyz: photo storage is '${storage}'" >&2
    return 1
}
