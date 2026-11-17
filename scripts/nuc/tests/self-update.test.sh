#!/usr/bin/env bash
# Tests for scripts/nuc/lib/self-update.sh: a deploy script that pulls a new copy of
# itself must carry on with the new copy. Runs anywhere with bash and git; never
# touches /opt or /etc. Not wired into CI yet.
# Usage: bash scripts/nuc/tests/self-update.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HERE}/../lib/self-update.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

# A deploy script shaped like 40-update.sh: snapshot, pull, re-run if changed, then work.
deploy_script() { # deploy_script <version>
    cat <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${LIB}"
self_update_snapshot
echo "start $1 args=\$*"
git -C "\$(dirname "\$0")" pull -q --ff-only
self_update_rerun_if_changed "\$@"
echo "after-pull $1"
EOF
}

setup() {
    rm -rf "${WORK:?}"/*
    git init -q --bare "${WORK}/origin.git"
    git clone -q "${WORK}/origin.git" "${WORK}/deploy" 2>/dev/null
    git clone -q "${WORK}/origin.git" "${WORK}/dev" 2>/dev/null
    for d in deploy dev; do git -C "${WORK}/$d" config user.email t@example.com; git -C "${WORK}/$d" config user.name t; done
    deploy_script v1 > "${WORK}/dev/update.sh"; chmod +x "${WORK}/dev/update.sh"
    git -C "${WORK}/dev" add update.sh && git -C "${WORK}/dev" commit -qm v1 && git -C "${WORK}/dev" push -q origin HEAD:main 2>/dev/null
    git -C "${WORK}/deploy" pull -q origin main 2>/dev/null; git -C "${WORK}/deploy" branch -q -u origin/main 2>/dev/null || true
}

echo "self-update: the pulled script changed"
setup
deploy_script v2 > "${WORK}/dev/update.sh"
git -C "${WORK}/dev" commit -qam v2 && git -C "${WORK}/dev" push -q origin HEAD:main 2>/dev/null
OUT=$("${WORK}/deploy/update.sh" abc 2>&1 || true)
check "runs the rest of the deploy with the new copy" grep -qx "after-pull v2" <<<"${OUT}"
check "never runs the rest with the old copy" bash -c '! grep -qx "after-pull v1" <<<"$1"' _ "${OUT}"
check "says why it restarted" grep -q "changed in this update; running the new copy" <<<"${OUT}"
check "keeps the script's arguments" grep -qx "start v2 args=abc" <<<"${OUT}"
check "restarts once, not in a loop" test "$(grep -c '^start ' <<<"${OUT}")" -eq 2

echo "self-update: the pulled script is the same"
setup
echo "notes" > "${WORK}/dev/README"; git -C "${WORK}/dev" add README
git -C "${WORK}/dev" commit -qm other && git -C "${WORK}/dev" push -q origin HEAD:main 2>/dev/null
OUT=$("${WORK}/deploy/update.sh" 2>&1 || true)
check "does not restart" test "$(grep -c '^start ' <<<"${OUT}")" -eq 1
check "carries on with the running copy" grep -qx "after-pull v1" <<<"${OUT}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "All self-update tests passed."; else echo "${FAILS} self-update test(s) failed."; exit 1; fi
