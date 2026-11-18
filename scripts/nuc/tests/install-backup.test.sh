#!/usr/bin/env bash
# Tests for scripts/nuc/30-backup.sh, the backup installer (tracker row 36).
# systemctl and age-keygen are PATH shims; paths point into a temp dir.
# Usage: bash scripts/nuc/tests/install-backup.test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="${HERE}/../30-backup.sh"
TOP="$(mktemp -d)"
trap 'rm -rf "${TOP}"' EXIT

FAILS=0
check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then echo "  ok   ${desc}"; else echo "  FAIL ${desc}"; FAILS=$((FAILS + 1)); fi
}

setup() {
    WORK="$(mktemp -d "${TOP}/w.XXXX")"
    mkdir -p "${WORK}/bin" "${WORK}/etc/perfect-day" "${WORK}/backups" "${WORK}/systemd" "${WORK}/log"
    echo "old dump" > "${WORK}/backups/pre-x.sql.gz"; chmod 644 "${WORK}/backups/pre-x.sql.gz"
    chmod 755 "${WORK}/backups"
    cat > "${WORK}/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "${CALLS}"
EOF
    cat > "${WORK}/bin/age-keygen" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-o" ]; then printf '# public key: age1testpublic\nAGE-SECRET-KEY-1TESTPRIVATE\n' > "$2"; exit 0; fi
if [ "$1" = "-y" ]; then echo "age1testpublic"; exit 0; fi
exit 1
EOF
    chmod +x "${WORK}/bin/"*
    export PATH="${WORK}/bin:${PATH}" CALLS="${WORK}/systemctl.calls" PD_INSTALL_ALLOW_NONROOT=1
    export PD_BACKUP_ROOT="${WORK}/backups" PD_ETC="${WORK}/etc/perfect-day" PD_LOG_DIR="${WORK}/log" \
        PD_SYSTEMD_DIR="${WORK}/systemd" PD_DEPLOY_DIR="/opt/perfect-day"
    touch "${CALLS}"
}

echo "first_install_requires_saved"
setup
RC=0; OUT=$(bash "${INSTALL}" <<<"nope" 2>&1) || RC=$?
check "exits 1 without 'saved'" test "${RC}" -eq 1
check "never enables the timer" bash -c '! grep -q enable "$1"' _ "${CALLS}"
check "removes the unconfirmed key so the next run shows a new one" test ! -e "${PD_ETC}/backup.key"

echo "first install, confirmed"
setup
RC=0; OUT=$(bash "${INSTALL}" <<<"saved" 2>&1) || RC=$?
check "exits 0" test "${RC}" -eq 0
check "shows the private key once" test "$(grep -c 'AGE-SECRET-KEY-1TESTPRIVATE' <<<"${OUT}")" -eq 1
check "key is private" test "$(stat -c %a "${PD_ETC}/backup.key")" = "600"
check "public key written" grep -qx "age1testpublic" "${PD_ETC}/backup.pub"
check "enables the timer" grep -q "enable --now perfect-day-backup.timer" "${CALLS}"
check "reminds about MASTER_SECRET" grep -q "MASTER_SECRET" <<<"${OUT}"
check "backup folder is 700" test "$(stat -c %a "${PD_BACKUP_ROOT}")" = "700"
check "old dumps are 600" test "$(stat -c %a "${PD_BACKUP_ROOT}/pre-x.sql.gz")" = "600"
check "nightly folder made" test -d "${PD_BACKUP_ROOT}/nightly"

echo "unit_files_content"
SVC="${PD_SYSTEMD_DIR}/perfect-day-backup.service"; TMR="${PD_SYSTEMD_DIR}/perfect-day-backup.timer"
check "runs backup-run.sh from the deploy dir" grep -qx "ExecStart=/opt/perfect-day/scripts/nuc/backup-run.sh" "${SVC}"
check "oneshot" grep -qx "Type=oneshot" "${SVC}"
check "15-minute cap" grep -qx "TimeoutStartSec=15min" "${SVC}"
check "does not also append stdout to backup.log" bash -c '! grep -q "backup.log" "$1"' _ "${SVC}"
check "daily at 02:17" grep -qx "OnCalendar=\*-\*-\* 02:17:00" "${TMR}"
check "catches up after downtime" grep -qx "Persistent=true" "${TMR}"

echo "never_starts_service"
check "never starts a backup run" bash -c '! grep -q "start perfect-day-backup.service" "$1"' _ "${CALLS}"

echo "rerun_keeps_existing_key"
BEFORE="$(sha256sum "${PD_ETC}/backup.key")"
RC=0; OUT=$(bash "${INSTALL}" </dev/null 2>&1) || RC=$?
check "exits 0 without asking" test "${RC}" -eq 0
check "key unchanged" test "$(sha256sum "${PD_ETC}/backup.key")" = "${BEFORE}"
check "key not shown again" bash -c '! grep -q "AGE-SECRET-KEY" <<<"$1"' _ "${OUT}"

echo "no_rclone_or_b2"
check "no Backblaze or rclone left" bash -c '! grep -qiE "rclone|backblaze|\bb2\b" "$1"' _ "${INSTALL}"

echo ""
if [ "${FAILS}" -eq 0 ]; then echo "install-backup: all passed"; else echo "install-backup: ${FAILS} failed"; exit 1; fi
