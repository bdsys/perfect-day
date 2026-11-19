#!/usr/bin/env bash
# scripts/nuke-dev.sh — wipe the LOCAL dev stack and start over (`make nuke-dev`).
#
# Removes the perfect-day compose project's containers and volumes on this machine
# (database, photo store, pgAdmin) and the e2e scratch folders /tmp/pd-test-*, so
# the next `make up && make migrate` starts empty. Images are never removed.
#
# Refuses on the NUC (where /etc/perfect-day exists) and whenever Docker points
# anywhere but this machine (DOCKER_HOST set, or a context other than "default").
# Lists what it will delete and asks you to type "nuke".
#
# Not in scripts/admin.sh on purpose: that is the production tool on the NUC.
# PD_ETC and PD_TEST_TMP_GLOB are for its tests (scripts/tests/nuke-dev.test.sh).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PD_ETC="${PD_ETC:-/etc/perfect-day}"
PD_TEST_TMP_GLOB="${PD_TEST_TMP_GLOB:-/tmp/pd-test-*}"
PROJECT="perfect-day"

refuse() { echo "nuke-dev: REFUSED: $*" >&2; exit 2; }

[ -e "${PD_ETC}" ] && refuse "${PD_ETC} exists, so this looks like the NUC. This wipes dev only."
[ -n "${DOCKER_HOST:-}" ] && refuse "DOCKER_HOST is set (${DOCKER_HOST}); Docker may not be this machine."
ctx="$(docker context show)"
[ "${ctx}" = default ] || refuse "Docker's context is '${ctx}', not 'default'; it may not be this machine."

mapfile -t containers < <(docker ps -a --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Names}}')
mapfile -t volumes < <(docker volume ls --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Name}}')
folders=()
for f in ${PD_TEST_TMP_GLOB}; do [ -e "${f}" ] && folders+=("${f}"); done

echo "This deletes, on this machine only:"
echo "  containers: ${containers[*]:-(none)}"
echo "  volumes:    ${volumes[*]:-(none)}"
echo "  folders:    ${folders[*]:-(none)}"
echo "Images are kept. Type \"nuke\" to go on:"
read -r answer || true
[ "${answer:-}" = nuke ] || { echo "Nothing removed."; exit 1; }

(cd "${REPO_ROOT}" && docker compose -p "${PROJECT}" -f docker-compose.yml -f docker-compose.dev.yml \
    --profile dev down -v --remove-orphans)
for v in "${volumes[@]}"; do
    docker volume rm "${v}" >/dev/null 2>&1 || true   # down -v already took most
done
# Docker made these folders as root, so remove them through a container, by exact path.
for f in "${folders[@]}"; do
    docker run --rm -v "$(dirname "${f}"):/p" alpine rm -rf "/p/$(basename "${f}")"
done

echo "Dev is empty. Next: make up && make migrate"
