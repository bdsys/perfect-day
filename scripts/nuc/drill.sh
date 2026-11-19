#!/usr/bin/env bash
# scripts/nuc/drill.sh — prove a backup restores (tracker row 36).
#
# Usage: sudo ./scripts/nuc/drill.sh [YYYY-MM-DD]   (default: the newest night on the NAS)
#
# Fetches the night from the NAS, restores it into a throwaway compose project
# (perfect-day-drill: postgres and the photo store only, nothing published, so nothing scans
# Google, calls Anthropic, sends email or touches DNS), then checks that:
#   - the backup's MASTER_SECRET and OAUTH_TOKEN_SECRET are the live ones (other
#     settings that differ are named, not failed on),
#   - row and object counts match the backup's manifest,
#   - photos and avatars decrypt (manage.py photos verify),
#   - only postgres and the store S3_ENDPOINT_URL names ran, with no published ports.
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

# The drill restores with the live /etc/perfect-day/app.env, so it has only tested the
# backup if the keys the data is encrypted under are the live ones: MASTER_SECRET
# (photos, avatars) and OAUTH_TOKEN_SECRET (Google tokens). Other settings come and go
# over the year a night is kept (GARAGE_RPC_SECRET was added, the MINIO_* lines removed,
# row 41), so they are named, never failed on. Values are never printed.
age -d -i "${PD_KEY}" < "${TMP}/night/config.tar.age" | tar -xOf - "${PD_ETC#/}/app.env" > "${TMP}/app.env" \
    || REASONS+=("the config backup has no app.env")
for key in MASTER_SECRET OAUTH_TOKEN_SECRET; do
    was="$(python3 "${PD_ENVFILE_PY}" get "${TMP}/app.env" "${key}" 2>/dev/null || echo "<absent>")"
    now="$(python3 "${PD_ENVFILE_PY}" get "${PD_ETC}/app.env" "${key}" 2>/dev/null || echo "<absent>")"
    [ "${was}" = "${now}" ] || REASONS+=("${key} in the backup's app.env is not the live one")
done
unset was now
others="$(diff <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP}/app.env" | sort) \
               <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${PD_ETC}/app.env" | sort) \
          | sed -nE 's/^[<>] ([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' \
          | grep -vxE 'MASTER_SECRET|OAUTH_TOKEN_SECRET' | sort -u | tr '\n' ' ')" || true
[ -n "${others}" ] && echo "Drill: note: these settings differ from the live config (no key; the live ones are used): ${others}"
STORE="$(storage_service_from_env "${PD_ETC}/app.env")" || { echo "DRILL FAILED: unknown photo store"; exit 1; }

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
expected="$(printf '%s\n' postgres "${STORE}" | sort | tr '\n' ' ')"
[ "${running}" = "${expected}" ] || REASONS+=("expected only postgres and ${STORE} running, saw: ${running}")
if docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Ports}}' | grep -q -- '->'; then
    REASONS+=("a drill container published a port")
fi

if [ "${#REASONS[@]}" -eq 0 ]; then
    echo "DRILL PASSED (${DATE})"
else
    echo "DRILL FAILED: $(IFS='; '; echo "${REASONS[*]}")"
    exit 1
fi
