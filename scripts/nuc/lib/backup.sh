#!/usr/bin/env bash
# Shared functions for the nightly backup, restore and drill (tracker row 36).
# Design: docs/superpowers/specs/2026-11-18-nuc-backups-design.md
#
# Every path comes from the environment with the production default, so the
# tests in scripts/nuc/tests/ can point them at a temp dir.
# shellcheck disable=SC2034  # variables are used by the scripts that source this

PD_BACKUP_ROOT="${PD_BACKUP_ROOT:-/var/backups/perfect-day}"
PD_ETC="${PD_ETC:-/etc/perfect-day}"
PD_LOG_DIR="${PD_LOG_DIR:-/var/log/perfect-day}"
PD_DEPLOY_DIR="${PD_DEPLOY_DIR:-/opt/perfect-day}"
PD_NAS_URL="${PD_NAS_URL:-rsync://192.168.1.112/Backups/perfect-day/nightly/}"
PD_MIN_FREE_KB="${PD_MIN_FREE_KB:-2097152}"
PD_LOCK="${PD_LOCK:-/run/perfect-day-backup.lock}"

PD_NIGHTLY="${PD_BACKUP_ROOT}/nightly"
PD_STATUS="${PD_LOG_DIR}/backup-status.json"
PD_PUB="${PD_ETC}/backup.pub"
PD_KEY="${PD_ETC}/backup.key"
PD_KEEP_NEWEST=30
PD_KEEP_MONTHS=12

backup_log() { echo "[backup $(date -u +%FT%TZ)] $*"; }

# docker compose for the production project, run from the deploy dir.
pd_compose() { (cd "${PD_DEPLOY_DIR}" && docker compose --profile nuc "$@"); }

PD_ENVFILE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/envfile.py"

# storage_service_from_env <env file>: the compose service the app's photo store is,
# from the host in S3_ENDPOINT_URL (garage; garage when unset, as compose's
# default). Returns 1 for anything else. Tracker row 41.
storage_service_from_env() {
    local url
    url="$(python3 "${PD_ENVFILE_PY}" get "$1" S3_ENDPOINT_URL 2>/dev/null || true)"
    case "${url}" in
        "") echo garage ;;
        http://garage:*) echo garage ;;
        # Nights from before the move to Garage (2026-11-18) name the old store; the
        # restore then finds no such service here and points the app at Garage.
        http://minio:*) echo minio ;;
        *) echo "unknown photo store in S3_ENDPOINT_URL: ${url}" >&2; return 1 ;;
    esac
}

# compose_serves_store <compose file> <env file>: 0 if that compose file can run the
# photo store the env file names: it defines that service, and pins no other store's
# address in the app services. 50-rollback.sh checks the previous release this way,
# because before row 41 the compose file hard-coded http://minio:9000.
compose_serves_store() {
    local store pinned host
    store="$(storage_service_from_env "$2")" || return 1
    if ! grep -qE "^  ${store}:" "$1"; then
        echo "that compose file has no ${store} service, and this machine's photos are in ${store}" >&2
        return 1
    fi
    pinned="$(grep -oE 'S3_ENDPOINT_URL: *http://[A-Za-z0-9_-]+' "$1" | sed -E 's|.*http://||' | sort -u)"
    for host in ${pinned}; do
        if [ "${host}" != "${store}" ]; then
            echo "that compose file points the app at ${host}, and this machine's photos are in ${store}" >&2
            return 1
        fi
    done
}

# backup_keep_list <today YYYY-MM-DD> <folder names...>
# Prints the folder names to keep, sorted: the newest 30, plus the earliest of
# each calendar month among the 12 months ending with today's month. Names that
# are not dates are ignored (and never printed).
backup_keep_list() {
    local today="$1"; shift
    local -a dates=()
    local n
    for n in "$@"; do
        [[ "${n}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && dates+=("${n}")
    done
    [ "${#dates[@]}" -eq 0 ] && return 0
    local sorted; sorted="$(printf '%s\n' "${dates[@]}" | sort -u)"
    {
        tail -n "${PD_KEEP_NEWEST}" <<<"${sorted}"
        local i month
        for i in $(seq 0 $((PD_KEEP_MONTHS - 1))); do
            month="$(date -d "${today:0:7}-01 -${i} month" +%Y-%m)"
            grep -m1 "^${month}-" <<<"${sorted}" || true
        done
    } | sort -u
}

# backup_clock_check <today> <folder names...>: succeeds if today's date can be
# trusted for retention: on or after the newest earlier night, and at most 35 days
# after it. Retention's monthly rule depends on today's date, so a wrong clock (an
# RTC reset, a new machine before time sync) would otherwise delete real backups.
# Prints the reason when it fails.
backup_clock_check() {
    local today="$1"; shift
    local newest
    newest="$(printf '%s\n' "$@" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | grep -vx "${today}" | sort | tail -n 1 || true)"
    [ -z "${newest}" ] && return 0
    if [[ "${today}" < "${newest}" ]]; then
        echo "the clock says ${today}, but a backup from ${newest} already exists"
        return 1
    fi
    local gap=$(( ( $(date -d "${today}" +%s) - $(date -d "${newest}" +%s) ) / 86400 ))
    if [ "${gap}" -gt 35 ]; then
        echo "the clock says ${today}, ${gap} days after the newest backup (${newest})"
        return 1
    fi
}

# backup_make_night <dir>: writes db.sql.gz.age.partial, photos.tar.age.partial,
# config.tar.age.partial and manifest.json.partial into <dir>. Returns non-zero
# with a reason on stderr if any step fails.
backup_make_night() {
    local dir="$1" started; started="$(date -u +%FT%TZ)"
    set -o pipefail

    backup_log "dumping the database"
    pd_compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
        | gzip | age -R "${PD_PUB}" > "${dir}/db.sql.gz.age.partial" \
        || { echo "database dump failed" >&2; return 1; }

    backup_log "exporting photo objects"
    pd_compose run --rm --no-deps -T -v "${PD_DEPLOY_DIR}/scripts:/scripts:ro" \
        -e PYTHONPATH=/app api python /scripts/manage.py photos export \
        2> "${dir}/export.err" | age -R "${PD_PUB}" > "${dir}/photos.tar.age.partial" \
        || { echo "photo export failed: $(tail -n 3 "${dir}/export.err")" >&2; return 1; }
    local stats; stats="$(grep '^{' "${dir}/export.err" | tail -n 1)"
    jq -e '.objects and (.bytes != null)' >/dev/null 2>&1 <<<"${stats}" \
        || { echo "photo export gave no object count" >&2; return 1; }
    rm -f "${dir}/export.err"

    backup_log "saving the server config"
    tar -C / --exclude="${PD_KEY#/}" -cf - "${PD_ETC#/}" \
        | age -R "${PD_PUB}" > "${dir}/config.tar.age.partial" \
        || { echo "config backup failed" >&2; return 1; }

    local rows
    rows="$(pd_compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select (select count(*) from users), (select count(*) from diaries), (select count(*) from entries), (select count(*) from photos)"')" \
        || { echo "row count failed" >&2; return 1; }
    local commit
    commit="$(git -c safe.directory="${PD_DEPLOY_DIR}" -C "${PD_DEPLOY_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"

    local files='{}' f
    for f in db.sql.gz.age photos.tar.age config.tar.age; do
        files="$(jq -c --arg f "${f}" --argjson b "$(stat -c %s "${dir}/${f}.partial")" \
            --arg s "$(sha256sum "${dir}/${f}.partial" | cut -d' ' -f1)" \
            '. + {($f): {bytes: $b, sha256: $s}}' <<<"${files}")"
    done
    IFS='|' read -r r_users r_diaries r_entries r_photos <<<"${rows}"
    jq -n --arg night "$(basename "${dir}" | sed 's/^\.//; s/\.partial$//')" \
        --arg started "${started}" --arg finished "$(date -u +%FT%TZ)" --arg commit "${commit}" \
        --argjson files "${files}" --argjson stats "${stats}" \
        --argjson u "${r_users}" --argjson d "${r_diaries}" --argjson e "${r_entries}" --argjson p "${r_photos}" \
        '{night: $night, started: $started, finished: $finished, git_commit: $commit, files: $files,
          objects: $stats.objects, object_bytes: $stats.bytes,
          rows: {users: $u, diaries: $d, entries: $e, photos: $p}}' \
        > "${dir}/manifest.json.partial"
}

# backup_check_files <dir> <key> [suffix]: decrypts each file of a night (with
# <suffix> appended to the names, e.g. ".partial") and checks it is whole: the
# dump ends with pg_dump's completion marker, the photo tar has the manifest's
# object count, and the config tar holds app.env. Prints the reason on failure.
backup_check_files() {
    local dir="$1" key="$2" sfx="${3:-}"
    set -o pipefail

    age -d -i "${key}" < "${dir}/db.sql.gz.age${sfx}" > /dev/null \
        || { echo "the backup could not be decrypted with ${key}" >&2; return 1; }

    age -d -i "${key}" < "${dir}/db.sql.gz.age${sfx}" | gunzip | grep -v '^[[:space:]]*$' \
        | tail -n 5 | grep -q '^-- PostgreSQL database dump complete' \
        || { echo "database dump is incomplete (no completion marker)" >&2; return 1; }

    local want got listing
    want="$(jq -r .objects "${dir}/manifest.json${sfx}")"
    listing="$(age -d -i "${key}" < "${dir}/photos.tar.age${sfx}" | tar -tvf -)" \
        || { echo "photo backup does not decrypt or unpack" >&2; return 1; }
    got="$(grep -c '^-' <<<"${listing}" || true)"
    [ "${got}" = "${want}" ] \
        || { echo "photo backup has ${got} objects, the export reported ${want}" >&2; return 1; }

    age -d -i "${key}" < "${dir}/config.tar.age${sfx}" | tar -tf - | grep -qx "${PD_ETC#/}/app.env" \
        || { echo "config backup has no app.env" >&2; return 1; }
}

# backup_verify_night <dir>: checks the night's .partial files with the backup key,
# and only then renames them into place.
backup_verify_night() {
    local dir="$1" f
    backup_check_files "${dir}" "${PD_KEY}" .partial || return 1
    for f in db.sql.gz.age photos.tar.age config.tar.age manifest.json; do
        mv "${dir}/${f}.partial" "${dir}/${f}"
    done
}

# backup_prune_local <today>: deletes dated nightly folders retention does not keep,
# and any half-made dot folder left by a killed run.
backup_prune_local() {
    local today="$1" keep n
    keep="$(backup_keep_list "${today}" $(ls -A "${PD_NIGHTLY}"))"
    for n in $(ls -A "${PD_NIGHTLY}"); do
        if [[ "${n}" == .* ]]; then rm -rf "${PD_NIGHTLY:?}/${n}"; continue; fi
        [[ "${n}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
        grep -qx "${n}" <<<"${keep}" || { backup_log "pruning ${n}"; rm -rf "${PD_NIGHTLY:?}/${n}"; }
    done
}

# backup_sync_nas <today> [--no-delete]: copies the local nightly folders to the NAS
# (adding and updating, never deleting), then applies the keep rule to everything
# the NAS holds, local and NAS nights together, and deletes only the NAS folders
# that rule drops. So a rebuilt machine with a few nights never trims the NAS's
# older history. Deletes nothing with --no-delete, or when the clock looks wrong
# against the NAS's nights (then RETENTION_SKIP says why).
backup_sync_nas() {
    local today="$1" no_delete="${2:-}" listing nas keep n rc=0 parent empty
    empty="$(mktemp -d)"
    # The copy below creates nightly/ but not its parent; make that first (once).
    parent="${PD_NAS_URL%/}"; parent="${parent%/*}/"
    rsync -r --timeout=60 "${empty}/" "${parent}" || rc=$?
    [ "${rc}" -eq 0 ] && { rsync -a --timeout=60 "${PD_NIGHTLY}/" "${PD_NAS_URL}" || rc=$?; }
    [ "${rc}" -eq 0 ] && { listing="$(rsync --list-only --timeout=60 "${PD_NAS_URL}")" || rc=$?; }
    if [ "${rc}" -ne 0 ] || [ "${no_delete}" = "--no-delete" ]; then
        rmdir "${empty}"; return "${rc}"
    fi
    nas="$(awk '{print $NF}' <<<"${listing}" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' || true)"
    if [ -n "${nas}" ]; then
        local why
        # shellcheck disable=SC2086  # word splitting of folder names is intended
        if ! why="$(backup_clock_check "${today}" ${nas})"; then
            RETENTION_SKIP="${why}"
            rmdir "${empty}"; return 0
        fi
        # shellcheck disable=SC2046,SC2086
        keep="$(backup_keep_list "${today}" ${nas} $(ls -A "${PD_NIGHTLY}"))"
        local -a drop=()
        for n in ${nas}; do
            grep -qx "${n}" <<<"${keep}" || drop+=("--include=/${n}/***")
        done
        if [ "${#drop[@]}" -gt 0 ]; then
            backup_log "removing ${#drop[@]} folder(s) from the NAS that retention no longer keeps"
            rsync -r --timeout=60 --delete "${drop[@]}" --exclude='*' "${empty}/" "${PD_NAS_URL}" || rc=$?
        fi
    fi
    rmdir "${empty}"
    return "${rc}"
}

# backup_write_status <result> [error]: updates backup-status.json atomically,
# keeping the last success times from earlier runs. Uses the globals NIGHT,
# LOCAL_OK and NAS_OK set by backup-run.sh.
backup_write_status() {
    local result="$1" error="${2:-}" now; now="$(date -u +%FT%TZ)"
    local prev='{}'
    [ -s "${PD_STATUS}" ] && prev="$(jq -c . "${PD_STATUS}" 2>/dev/null || echo '{}')"
    local sizes='{}'
    [ -f "${PD_NIGHTLY}/${NIGHT:-}/manifest.json" ] && [ "${LOCAL_OK:-}" = 1 ] \
        && sizes="$(jq -c '.files | map_values(.bytes)' "${PD_NIGHTLY}/${NIGHT}/manifest.json")"
    local tmp; tmp="$(mktemp "${PD_LOG_DIR}/.backup-status.XXXX")"
    jq -n --argjson prev "${prev}" --arg result "${result}" --arg error "${error}" --arg now "${now}" \
        --arg night "${NIGHT:-}" --argjson sizes "${sizes}" \
        --arg local_ok "${LOCAL_OK:-0}" --arg nas_ok "${NAS_OK:-0}" \
        '{last_attempt: $now, result: $result, error: (if $error == "" then null else $error end),
          night: $night, sizes: $sizes,
          last_local_success: (if $local_ok == "1" then $now else $prev.last_local_success end),
          last_nas_success: (if $nas_ok == "1" then $now else $prev.last_nas_success end)}' > "${tmp}"
    mv "${tmp}" "${PD_STATUS}"
}
