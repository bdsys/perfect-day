#!/usr/bin/env bash
# Tests for scripts/nuke-dev.sh, which wipes the local dev stack (Andrew asked for
# an easy way, 2026-11-18). docker is a PATH shim that logs its arguments; nothing
# real is removed. Usage: bash scripts/tests/nuke-dev.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NUKE="${HERE}/../nuke-dev.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    mkdir -p "${WORK}/bin" "${WORK}/tmp/pd-test-garage" "${WORK}/tmp/pd-test-postgres"
    cat > "${WORK}/bin/docker" <<'SHIM'
#!/usr/bin/env bash
echo "docker $*" >> "${CALLS}"
case "$*" in
    "context show") echo "${CTX:-default}" ;;
    "ps -a --filter label=com.docker.compose.project=perfect-day --format {{.Names}}")
        printf 'perfect-day-api-1\nperfect-day-garage-1\n' ;;
    "volume ls --filter label=com.docker.compose.project=perfect-day --format {{.Name}}")
        printf 'perfect-day_postgres_data\nperfect-day_garage_data\n' ;;
esac
exit 0
SHIM
    chmod +x "${WORK}/bin/docker"
    export PATH="${WORK}/bin:${PATH}" CALLS="${WORK}/calls"
    export PD_ETC="${WORK}/no-such-etc" PD_TEST_TMP_GLOB="${WORK}/tmp/pd-test-*"
    unset DOCKER_HOST CTX 2>/dev/null || true
    : > "${CALLS}"
}
run_nuke() { RC=0; OUT="$(bash "${NUKE}" <<<"$1" 2>&1)" || RC=$?; }
removed_anything() { grep -qE "down|volume rm| rm " "${CALLS}"; }

echo "refuses_on_a_nuc"
setup
mkdir -p "${WORK}/etc"; export PD_ETC="${WORK}/etc"
run_nuke nuke
check "exits 2" test "${RC}" -eq 2
check "removes nothing" bash -c '! grep -qE "down|volume rm| rm " "$1"' _ "${CALLS}"
check "says why" grep -q "NUC" <<<"${OUT}"

echo "refuses_with_docker_host"
setup
export DOCKER_HOST=ssh://nuc
run_nuke nuke
check "exits 2" test "${RC}" -eq 2
check "removes nothing" bash -c '! grep -qE "down|volume rm| rm " "$1"' _ "${CALLS}"
unset DOCKER_HOST

echo "refuses_non_default_context"
setup
export CTX=nuc
run_nuke nuke
check "exits 2" test "${RC}" -eq 2
check "removes nothing" bash -c '! grep -qE "down|volume rm| rm " "$1"' _ "${CALLS}"

echo "wrong_word_removes_nothing"
setup
run_nuke yes
check "exits 1" test "${RC}" -eq 1
check "removes nothing" bash -c '! grep -qE "down|volume rm| rm " "$1"' _ "${CALLS}"
check "listed what it would remove" grep -q "perfect-day_postgres_data" <<<"${OUT}"
check "listed the scratch folders" grep -q "pd-test-garage" <<<"${OUT}"

echo "nuke_removes_listed_volumes_only"
setup
run_nuke nuke
check "exits 0" test "${RC}" -eq 0
check "compose down with volumes" grep -q "compose .*down -v --remove-orphans" "${CALLS}"
check "removes postgres volume" grep -qx "docker volume rm perfect-day_postgres_data" "${CALLS}"
check "removes garage volume" grep -qx "docker volume rm perfect-day_garage_data" "${CALLS}"
check "removes only listed volumes" test "$(grep -c "volume rm" "${CALLS}")" -eq 2
check "removes the scratch folders by exact path" grep -q "${WORK}/tmp:/p alpine rm -rf /p/pd-test-garage" "${CALLS}"
check "says what next" grep -q "make up" <<<"${OUT}"

echo "never_touches_images"
check "no rmi, image or prune" bash -c '! grep -qE "rmi|image |prune" "$1"' _ "${CALLS}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "nuke-dev: all passed"; else echo "nuke-dev: ${FAILS} failed"; exit 1; fi
