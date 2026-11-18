#!/usr/bin/env bash
# scripts/nuc/drill.sh — prove a backup restores (tracker row 36).
#
# Usage: sudo ./scripts/nuc/drill.sh [YYYY-MM-DD]   (default: the newest night on the NAS)
#
# Fetches the night from the NAS, restores it into a throwaway compose project
# (perfect-day-drill: postgres and minio only, nothing published, so nothing scans
# Google, calls Anthropic, sends email or touches DNS), then checks that:
#   - the restored app.env matches the live one,
#   - row and object counts match the backup's manifest,
#   - photos and avatars decrypt (manage.py photos verify),
#   - only postgres and minio ran, with no published ports.
# The project and its volumes are always removed afterwards. The data never leaves
# this machine. Prints DRILL PASSED or DRILL FAILED: <reasons>.
set -euo pipefail
umask 077

HERE="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib/backup.sh
source "${HERE}/lib/backup.sh"

PROJECT="perfect-day-drill"
compose_p() { (cd "${PD_DEPLOY_DIR}" && docker compose -p "${PROJECT}" "$@"); }
RUN_API=(run --rm --no-deps -T -v "${PD_DEPLOY_DIR}/scripts:/scripts:ro" -e PYTHONPATH=/app api)

DATE="${1:-}"
if [ -z "${DATE}" ]; then
    DATE="$(rsync --list-only --timeout=60 "${PD_NAS_URL}" | awk '{print $NF}' \
        | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | tail -n 1)"
    [ -n "${DATE}" ] || { echo "DRILL FAILED: no nights found on ${PD_NAS_URL}"; exit 1; }
fi

mkdir -p "${PD_BACKUP_ROOT}"
TMP="$(mktemp -d "${PD_BACKUP_ROOT}/drill.XXXX")"
teardown() {
    compose_p down -v >/dev/null 2>&1 || true
    docker image rm "${PROJECT}-api:latest" >/dev/null 2>&1 || true  # the tag restore.sh borrowed
    rm -rf "${TMP}"
}
trap teardown EXIT

REASONS=()
echo "Drill: restoring ${DATE} from the NAS into ${PROJECT}"
rsync -a --timeout=60 "${PD_NAS_URL}${DATE}/" "${TMP}/night/" \
    || { echo "DRILL FAILED: could not fetch ${DATE} from the NAS"; exit 1; }
MANIFEST="${TMP}/night/manifest.json"

# The compose file reads the live /etc/perfect-day/app.env, so the restored one
# must be identical for this drill to have tested the backup's keys.
age -d -i "${PD_KEY}" < "${TMP}/night/config.tar.age" | tar -xOf - "${PD_ETC#/}/app.env" > "${TMP}/app.env" \
    || REASONS+=("the config backup has no app.env")
cmp -s "${TMP}/app.env" "${PD_ETC}/app.env" || REASONS+=("the restored app.env differs from the live one")

if "${HERE}/restore.sh" "${DATE}" --source-dir "${TMP}/night" --into "${PROJECT}" </dev/null; then
    rows="$(compose_p exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select (select count(*) from users), (select count(*) from diaries), (select count(*) from entries), (select count(*) from photos)"')"
    IFS='|' read -r n_users n_diaries n_entries n_photos <<<"${rows}"
    for t in users diaries entries photos; do
        got="n_${t}"; want="$(jq -r ".rows.${t}" "${MANIFEST}")"
        [ "${!got}" = "${want}" ] || REASONS+=("${t}: ${!got} rows restored, the backup has ${want}")
    done

    objects="$(compose_p "${RUN_API[@]}" python /scripts/manage.py photos export 2>/dev/null \
        | tar -tvf - | grep -c '^-' || true)"
    want="$(jq -r .objects "${MANIFEST}")"
    [ "${objects}" = "${want}" ] || REASONS+=("objects: ${objects} restored, the backup has ${want}")

    compose_p "${RUN_API[@]}" python /scripts/manage.py photos verify \
        || REASONS+=("photos verify failed: some photos or avatars did not decrypt")
else
    REASONS+=("restore.sh failed")
fi

running="$(compose_p ps --services --status running | sort | tr '\n' ' ')"
[ "${running}" = "minio postgres " ] || REASONS+=("expected only postgres and minio running, saw: ${running}")
if docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Ports}}' | grep -q -- '->'; then
    REASONS+=("a drill container published a port")
fi

if [ "${#REASONS[@]}" -eq 0 ]; then
    echo "DRILL PASSED (${DATE})"
else
    echo "DRILL FAILED: $(IFS='; '; echo "${REASONS[*]}")"
    exit 1
fi
