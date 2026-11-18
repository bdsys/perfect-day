#!/usr/bin/env bash
# Tests for scripts/nuc/lib/deploy-lock.sh: a deploy holds the backup lock, and a
# deploy that restarts itself with its new copy (lib/self-update.sh) keeps it
# instead of waiting on itself. Runs anywhere with bash and flock.
# Usage: bash scripts/nuc/tests/deploy-lock.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HERE}/../lib/deploy-lock.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

export PD_LOCK="${WORK}/lock" PD_LOCK_WAIT=3

# Shaped like 40-update.sh: take the lock, log through tee, restart once as the new copy.
cat > "${WORK}/deploy.sh" <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
source "${LIB}"
deploy_lock_take || { echo "lock wait failed"; exit 1; }
exec > >(tee -a "${WORK}/deploy.log") 2>&1
echo "run \${SELF_UPDATE_RERUN:-first}"
if [ -z "\${SELF_UPDATE_RERUN:-}" ]; then exec env SELF_UPDATE_RERUN=1 "\$0"; fi
if flock -n "${PD_LOCK}" true; then echo "lock free during the rerun"; else echo "lock held during the rerun"; fi
SCRIPT
chmod +x "${WORK}/deploy.sh"

echo "deploy that restarts itself"
START=$(date +%s)
OUT="$("${WORK}/deploy.sh" 2>&1 || true)"
sleep 0.2
LOGTXT="$(cat "${WORK}/deploy.log" 2>/dev/null || true)"
check "the restarted copy runs" grep -q "run 1" <<<"${OUT}${LOGTXT}"
check "it does not wait on its own lock" bash -c '! grep -q "lock wait failed" <<<"$1"' _ "${OUT}${LOGTXT}"
check "finishes well inside the wait" test $(( $(date +%s) - START )) -lt "${PD_LOCK_WAIT}"
check "still holds the lock while it works" grep -q "lock held during the rerun" <<<"${OUT}${LOGTXT}"

echo "a running backup holds the lock"
exec 7>"${PD_LOCK}"; flock -n 7
RC=0; ( source "${LIB}"; deploy_lock_take ) || RC=$?
exec 7>&-
check "the deploy gives up after the wait" test "${RC}" -ne 0

echo "lock free"
RC=0; ( source "${LIB}"; deploy_lock_take ) || RC=$?
check "the deploy takes it" test "${RC}" -eq 0

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "deploy-lock: all passed"; else echo "deploy-lock: ${FAILS} failed"; exit 1; fi
