# NUC Backups to the NAS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nightly, encrypted, verified backups of the database, every photo object and `/etc/perfect-day/`. They are copied to `rsync://192.168.1.112/Backups/perfect-day/nightly/`, with a restore script and a restore drill that has been run.

**Architecture:**
- Photo objects travel through the S3 interface. New `manage.py photos export|import|verify` commands run in a one-off API container, and their logic lives in `app/services/object_backup.py`, so integration tests cover it.
- The nightly job is plain bash. `scripts/nuc/backup-run.sh` is a thin entry point over testable functions in `scripts/nuc/lib/backup.sh`.
- systemd runs the job straight from `/opt/perfect-day`, so deploys update it.
- `scripts/nuc/restore.sh` and `scripts/nuc/drill.sh` reuse the same library.

**Tech Stack:** bash, age, rsync, systemd, Docker Compose, Python 3.12 (click, boto3, tarfile), pytest with the Chainguard MinIO testcontainer.

**Spec:** `docs/superpowers/specs/2026-11-18-nuc-backups-design.md` (approved by Andrew, 2026-11-18)

## Global Constraints

- NAS target: `rsync://192.168.1.112/Backups/perfect-day/nightly/`, an anonymous rsync daemon. No password, by Andrew's decision.
- Retention: the newest 30 nightly folders, plus the earliest good folder of each of the last 12 months.
- Schedule: daily at 02:17 (`OnCalendar=*-*-* 02:17:00`, `Persistent=true`).
- Local folder: `/var/backups/perfect-day/nightly/YYYY-MM-DD/`. `/var/backups/perfect-day` is mode 700, and everything is written under `umask 077`.
- Each night's files: `db.sql.gz.age`, `photos.tar.age`, `config.tar.age` and `manifest.json`. Files are written as `*.partial` and renamed only after verification.
- Status: `/var/log/perfect-day/backup-status.json` with `last_attempt`, `last_local_success`, `last_nas_success`, `result` (`ok` | `nas_failed` | `failed`), `error`, `night` and `sizes`. The log is `/var/log/perfect-day/backup.log`.
- Keys:
  - the public key is `/etc/perfect-day/backup.pub`;
  - the private key is `/etc/perfect-day/backup.key` (root, mode 600);
  - the private key is shown once, to Andrew only, in his own terminal. It is never printed into a Claude session, a log or a tool.
- Only `perfect-day-*` containers and the `perfect-day` compose project are touched. The drill project is named `perfect-day-drill` and runs **only postgres and minio**. One-off `api` containers use `--no-deps`. `worker`, `beat`, `edge` and `cloudflare-ddns` never start.
- Never run or call `10-secrets.sh`. Production steps on the NUC need Andrew's OK at the time.
- Only the encrypted `nightly/` folder goes to the NAS. Ad-hoc `pre-*.sql.gz` dumps never leave the NUC.
- Stage explicit paths only. Run `make test-all`, then the dev-stack check, before the PR merges.

## Review Focus

1. **The NAS is unreachable or hangs.** rsync runs with `--timeout=60` and the run takes at most 15 minutes in total. The night's local folder is kept, `result` is `nas_failed`, and the exit code is non-zero. Covered in Task 3 (`nas_unreachable_keeps_local_and_fails`).
2. **A run that fails part-way, or a rebuilt machine, deletes good backups.** Nothing is pruned unless the night verified. The NAS copy never deletes; afterwards only the NAS folders the keep rule drops, judged over the NAS history and the local nights together, are removed by name. Covered in Task 3 (`failed_run_prunes_nothing`, `rebuilt_machine_keeps_nas_history`, `tiny_local_set_never_deletes_on_nas`).
3. **A second run on the same day, or a run overlapping a deploy.** A second run finds the lock held and exits 75 with "already running". `40-update.sh` waits up to 30 minutes for the same lock. A same-day rerun replaces that night's folder atomically. Covered in Task 3 (`lock_held_exits_75`, `same_day_rerun_replaces_atomically`).
4. **Object keys with spaces, unicode or deep paths, an empty bucket, and more objects than one listing page.** These round-trip exactly. Covered in Task 1.
5. **The disk is nearly full.** The run refuses to start when less than 2 GB is free on `/var/backups`. Covered in Task 3 (`low_disk_refuses`).

---

### Task 1: Photo object export and import

**Files:**
- Create: `apps/api/app/services/object_backup.py`
- Modify: `scripts/manage.py` (new `photos` group with `export` and `import`)
- Test: `apps/api/tests/integration/test_object_backup.py`

**Interfaces:**
- Produces:
  - `export_bucket_tar(s3, bucket: str, out: BinaryIO, page_size: int = 1000) -> BackupStats`
  - `import_bucket_tar(s3, bucket: str, inp: BinaryIO) -> BackupStats`
  - `@dataclass(frozen=True) class BackupStats: objects: int; bytes: int`
- CLI:
  - `manage.py photos export` writes the tar to **stdout**, and on **stderr** one JSON line, `{"objects": N, "bytes": B}`.
  - `manage.py photos import` reads the tar from **stdin** and prints the same JSON line to stderr.
  - Both use `get_s3()` and `get_settings().s3_bucket_photos`.

Ruling for the tar format: a plain POSIX tar, member name = the object key, file data = the object bytes, nothing else. Content-type isn't carried, because every object is written as `application/octet-stream` (`services/photos.py:249`), and import writes that same type. Import creates the bucket if it's missing, overwrites objects with the same name, and leaves other objects in place. It rejects tar members that aren't regular files.

- [ ] **Step 1: Write the failing tests** in `test_object_backup.py`, using the `s3_client` fixture and two freshly created buckets per test:
  - `test_round_trip_keys_and_bytes`: these keys go in, and after export and import into an empty second bucket the key→bytes map is identical and `BackupStats(objects=4, bytes=<sum>)`:
    - `u1/p1.bin`
    - `u1/p1.display.bin`
    - `u 2/fötö/deep/a/b/c.bin`
    - `avatars/x.bin` (one of them 0 bytes)
  - `test_listing_pages`: 5 objects with `page_size=2` all export.
  - `test_empty_bucket`: export gives a valid tar with 0 members and `BackupStats(0, 0)`.
  - `test_import_creates_missing_bucket` and `test_import_overwrites_same_key_keeps_others`.
  - `test_import_rejects_non_file_member`: a tar with a symlink member raises `ValueError`.
  - `test_cli_export_import_json_stats`: through `CliRunner`, with the `manage` fixture pattern from `test_manage_cli.py`. Stdout is a tar, and stderr's last line parses to `{"objects": …, "bytes": …}`.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_object_backup.py -q`. Expected: FAIL, because `object_backup` doesn't exist yet.
- [ ] **Step 3: Implement** `object_backup.py` with a `list_objects_v2` paginator (`PaginationConfig={"PageSize": page_size}`) and `tarfile` in stream mode (`w|`, `r|`), plus the `photos` click group in `manage.py`. Write binary output to `sys.stdout.buffer`.
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): photos export/import through the S3 interface (row 36)`.

### Task 2: Restored-photo verification

**Files:**
- Create: `apps/api/app/services/backup_verify.py`
- Modify: `scripts/manage.py` (`photos verify`)
- Test: `apps/api/tests/integration/test_backup_verify.py`

**Interfaces:**
- Produces:
  - `verify_photo_sample(db: Session, limit: int = 20) -> VerifyReport`
  - `@dataclass class VerifyReport: checked: int; failures: list[str]`, where each failure is `"<kind> <id>: <reason>"` and never contains photo bytes.
  - CLI: `manage.py photos verify [--limit 20]` prints `checked=N failures=M` followed by each failure, and exits 1 if `M > 0` or `N == 0` while photo or avatar rows exist.
- Consumes: `unwrap_dek`, `decrypt_stream`, `PURPOSE_FULL` (`app/core/photo_crypto.py`), `load_avatar` (`app/services/avatar.py`) and `get_object_bytes` (`app/services/photos.py`).
- Checks:
  - every user with `avatar_s3_key` decrypts through `load_avatar`;
  - up to `limit` photos where `deleted_at IS NULL AND finalized_at IS NOT NULL` (newest first) unwrap with `unwrap_dek(dek_ciphertext, user_id)` and decrypt their `s3_key` object with `PURPOSE_FULL`.

- [ ] **Step 1: Write the failing tests.** Photos and avatars are made with the app's real encrypt helpers (`wrap_dek`/`encrypt_stream`, and `store_avatar`):
  - `test_all_good`: `checked=3, failures=[]` for 2 photos and 1 avatar.
  - `test_corrupted_object_reported`: one object's bytes are overwritten, so exactly 1 failure names that photo's id.
  - `test_missing_object_reported`.
  - `test_wrong_master_secret_fails_all`: monkeypatch the settings' `master_secret`, and every check fails.
  - `test_limit_respected`: 25 photos with `limit=20` check 20 photos plus the avatars.
  - `test_cli_exit_codes`: exit 0 when all good, exit 1 with a failure.
- [ ] **Step 2: Run** `cd apps/api && .venv/bin/pytest tests/integration/test_backup_verify.py -q`. Expected: FAIL (the import is missing).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): photos verify decrypts a sample with the current keys (row 36)`.

### Task 3: The nightly job

**Files:**
- Create: `scripts/nuc/lib/backup.sh`, `scripts/nuc/backup-run.sh` and `scripts/nuc/tests/backup.test.sh`
- Modify: `scripts/nuc/40-update.sh` (take the backup lock), `Makefile` (a `test-scripts` target that runs every `scripts/nuc/tests/*.test.sh`, added to `test-all` after `lint`), and `.github/workflows/ci.yml` (run `make test-scripts` in the Lint job)

**Interfaces** (`lib/backup.sh` functions; every path comes from env with production defaults, so tests point them at a temp dir):
- Variables and their defaults:
  - `PD_BACKUP_ROOT` → `/var/backups/perfect-day`
  - `PD_ETC` → `/etc/perfect-day`
  - `PD_LOG_DIR` → `/var/log/perfect-day`
  - `PD_DEPLOY_DIR` → `/opt/perfect-day`
  - `PD_NAS_URL` → `rsync://192.168.1.112/Backups/perfect-day/nightly/`
  - `PD_MIN_FREE_KB` → `2097152`
  - `PD_LOCK` → `/run/perfect-day-backup.lock`
- `backup_keep_list <today YYYY-MM-DD> <folder names...>`: prints the names to keep, one per line, sorted. It keeps the newest 30, plus the earliest of each calendar month among the 12 months ending with today's month.
- `backup_make_night <night_dir>`: writes the three `.age.partial` files and `manifest.json.partial`. In order:
  1. `docker compose --project-directory "$PD_DEPLOY_DIR" exec -T postgres pg_dump …`, then gzip, then `age -R backup.pub`.
  2. `docker compose … run --rm --no-deps -T -v scripts:/scripts:ro -e PYTHONPATH=/app api python /scripts/manage.py photos export`, then age. Its stderr JSON goes into the manifest.
  3. `tar -C / etc/perfect-day`, then age. This excludes `backup.key`: the backup must not contain the key that decrypts it.
- The manifest fields:
  - `night`, `started`, `finished`, `git_commit`;
  - `files` (name → `{bytes, sha256}`);
  - `objects`, `object_bytes`;
  - `rows` (users, diaries, entries, photos; counted with psql inside the postgres container).
- `backup_verify_night <night_dir>`: decrypts with `PD_ETC/backup.key` and checks:
  - the dump's last non-empty line starts with `-- PostgreSQL database dump complete`, or is pg_dump's final `\unrestrict` line preceded by that marker;
  - `tar -t` of the photos counts exactly `objects` regular members;
  - the config tar contains `etc/perfect-day/app.env`.

  Only then are the `.partial` files renamed.
- `backup_prune_local` and `backup_sync_nas`: rsync is called as `rsync -a --timeout=60 --delete "$PD_BACKUP_ROOT/nightly/" "$PD_NAS_URL"`, and only when the local set has 2 or more folders; otherwise without `--delete`.
- `backup_write_status <result> [error]`: writes the status JSON atomically, via a temp file and `mv`.
- `backup-run.sh`:
  1. Set `umask 077`, take `flock -n 9` on `PD_LOCK` (exit 75 with "already running" if held), and check free disk.
  2. Make the night into `nightly/.YYYY-MM-DD.partial/`, verify it, then `mv` it over `nightly/YYYY-MM-DD`.
  3. Prune, then sync.
  4. Write the status and log everything to `backup.log`.

  Exit codes: 0 when `ok`, 1 on `failed`, 2 on `nas_failed`. The whole run sits under `timeout 15m`.
- `40-update.sh`: wraps its build-and-restart section in `flock -w 1800 /run/perfect-day-backup.lock`.

Tests use PATH shims in a temp `bin/` for `docker`, `rsync`, `age`, `df` and `date` (`TODAY` overridable). Each shim records its arguments to a file. The style follows `self-update.test.sh`, with `check` lines and a `FAILS` count.

- [ ] **Step 1: Write the failing tests** in `backup.test.sh`:
  - `keep_list_400_days`: folders for every day from 2025-10-15 to 2026-11-18 keep exactly the newest 30, plus the 1st of each of the 12 months from 2025-12 to 2026-11 that isn't already among the 30. Assert the exact list.
  - `keep_list_first_run`: one folder → it is kept.
  - `verify_rejects_truncated_dump` (no completion marker → non-zero, no `.partial` renamed), `verify_rejects_object_count_mismatch` and `verify_rejects_config_without_app_env`.
  - `config_tar_excludes_backup_key`.
  - `failed_run_prunes_nothing`: the docker shim fails pg_dump. Old folders remain, rsync is never called, `result=failed`, exit 1.
  - `nas_unreachable_keeps_local_and_fails`: the rsync shim exits 10. The night folder exists, `result=nas_failed`, `last_local_success` is set, `last_nas_success` is unchanged, exit 2.
  - `tiny_local_set_never_deletes_on_nas`: with 1 local folder, rsync args contain no `--delete`.
  - `lock_held_exits_75`.
  - `same_day_rerun_replaces_atomically`: an existing `nightly/2026-11-18/` is replaced, and no `.partial` remains.
  - `low_disk_refuses`: the df shim reports 1 GB free → exit 1, `result=failed`, error mentions disk.
  - `status_json_fields`: after a good run, `jq` finds every field listed in Global Constraints.
- [ ] **Step 2: Run** `bash scripts/nuc/tests/backup.test.sh`. Expected: FAILs (the library is missing).
- [ ] **Step 3: Implement** `lib/backup.sh`, `backup-run.sh`, the `40-update.sh` lock, the `make test-scripts` target, and the CI step.
- [ ] **Step 4: Run** `make test-scripts`. Expected: all three test files pass (self-update, update-config, backup).
- [ ] **Step 5: Commit** `feat(nuc): nightly encrypted backup job with retention and NAS copy (row 36)`.

### Task 4: Restore and drill

**Files:**
- Create: `scripts/nuc/restore.sh` and `scripts/nuc/drill.sh`
- Modify: `scripts/nuc/lib/backup.sh` (restore helpers)
- Test: `scripts/nuc/tests/restore.test.sh`

**Interfaces:**
- `restore.sh <YYYY-MM-DD> [--from-nas] [--into <project>] [--with-config] [--key <path>]`:
  - **Source:** with `--from-nas`, rsync `"$PD_NAS_URL<date>/"` into a `mktemp -d` under `PD_BACKUP_ROOT` (mode 700); otherwise `nightly/<date>/`. Check every file's sha256 against `manifest.json` before using anything.
  - **`--into <project>`:** refuses `perfect-day`. It uses `docker compose -p <project> --project-directory "$PD_DEPLOY_DIR"` against that project's postgres and minio, and the database is restored into an empty database.
  - **No `--into` (production):**
    1. require the date typed back, then take an ad-hoc dump to `pre-restore-<ts>.sql.gz`, mode 600;
    2. `docker compose --profile nuc stop api worker beat`;
    3. drop and recreate the database, restore, and `photos import`;
    4. `docker compose --profile nuc up -d api worker beat` and run `scripts/smoke-test.sh`.
  - **`--with-config`:** extracts `config.tar` over `/` only after a typed confirmation, and saves the old `/etc/perfect-day` to `/etc/perfect-day.pre-restore-<ts>` first.
- `drill.sh [<YYYY-MM-DD>]` (defaults to the newest folder on the NAS):
  1. `restore.sh <date> --from-nas --into perfect-day-drill`. That runs `docker compose -p perfect-day-drill up -d postgres minio`, then creates the bucket via `photos import`.
  2. Compare row counts and object count with the manifest.
  3. Run `photos verify` in a one-off `--no-deps` api container in that project.
  4. Assert `docker compose -p perfect-day-drill ps` shows only postgres and minio, and that `docker port` shows nothing published.
  5. On exit (`trap`): `docker compose -p perfect-day-drill down -v`, and remove the temp dir.
  6. Print `DRILL PASSED` or `DRILL FAILED: <reasons>`.

  The drill uses the NUC's live `/etc/perfect-day/app.env` (the compose `env_file`). It diffs the restored `app.env` against the live one, and any difference fails the drill. Ruling: compose's `env_file` path is fixed, and on the NUC the restored and live configs must be identical, so the diff is the check.

- [ ] **Step 1: Write the failing tests** (`restore.test.sh`, same shim style):
  - `into_production_project_name_refused`: `--into perfect-day` → exit 1, and no docker calls.
  - `production_restore_needs_typed_date`: a wrong date on stdin → exit 1, and no `stop` call.
  - `production_restore_order`: the docker calls go dump → stop → restore → import → up → smoke, in that order.
  - `checksum_mismatch_aborts`: an altered file → exit 1 before any docker call.
  - `with_config_saves_old_etc_first`.
  - `drill_tears_down_on_failure`: verify fails, and `down -v` is still called.
  - `drill_starts_only_postgres_minio`: the `up` args contain exactly `postgres minio`.
  - `drill_fails_on_config_diff`.
- [ ] **Step 2: Run** `bash scripts/nuc/tests/restore.test.sh`. Expected: FAILs.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test-scripts`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(nuc): restore script and restore drill (row 36)`.

### Task 5: Installer, runbook and tracker

**Files:**
- Modify:
  - `scripts/nuc/30-backup.sh` (rewritten)
  - `deploy/nuc-ops.md` § B7
  - `deploy/nuc.md` § Backup
  - `CLAUDE.md` (the "NUC operations" bullet that says nothing is backed up)
  - `POC_PHASE2_TODO.md` row 36
- Test: `scripts/nuc/tests/install-backup.test.sh`

**Interfaces:** `30-backup.sh` (root only; it honours the `PD_ETC` and `PD_SYSTEMD_DIR` overrides, default `/etc/systemd/system`, for tests):
1. Set `/var/backups/perfect-day` to 700 and its files to 600, and make `nightly/`.
2. Create the age keypair only if `backup.key` is missing.
3. When it creates one, print the private key **once**. Then require Andrew to type `saved`; anything else exits 1 before the timer is enabled.
4. Write `perfect-day-backup.service` (`ExecStart=/opt/perfect-day/scripts/nuc/backup-run.sh`, `Type=oneshot`, the log appended to `backup.log`) and `perfect-day-backup.timer` (02:17, `Persistent=true`).
5. Run `systemctl daemon-reload` and `enable --now` on the **timer** only. Never start a backup run.

Remove every B2/rclone line.

- [ ] **Step 1: Write the failing tests** (`systemctl` and `age-keygen` shims):
  - `first_install_requires_saved`;
  - `rerun_keeps_existing_key` (key bytes unchanged, no key printed);
  - `unit_files_content` (ExecStart path, `OnCalendar=*-*-* 02:17:00`, `Persistent=true`);
  - `never_starts_service`;
  - `no_rclone_or_b2`: grep the script.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, and write the docs:
  - B7: install, run by hand, read the status, restore, drill, the "losing the NUC" fresh-machine order from the spec, and the ad-hoc dump command with `umask 077`;
  - CLAUDE.md;
  - tracker row 36 → **built**.

  The fresh-machine order relies on `20-deploy.sh` only requiring that `/etc/perfect-day/app.env` exists (`20-deploy.sh:96-99`); it never regenerates secrets (checked 2026-11-18). B7 must say to skip `10-secrets.sh`, even though `00-bootstrap.sh` names it as the next step.
- [ ] **Step 4: Run** `make test-scripts`, then `make test-all`, then the dev-stack check (`make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`). Expected: all green.
- [ ] **Step 5: Commit** `feat(nuc): backup installer and runbook (row 36)`, then push and open the PR. Merge on green CI, under Andrew's standing OK.

### Task 6: Production rollout (each step needs Andrew's OK at the time)

1. Ask for OK, dump the database (`umask 077`), and run `sudo ./scripts/nuc/40-update.sh` to bring the scripts to the NUC. Smoke must pass 17/0.
2. **Andrew runs `sudo /opt/perfect-day/scripts/nuc/30-backup.sh` in his own terminal**, not through Claude, saves the private key and `MASTER_SECRET` in his password manager, and types `saved`.
3. With OK, run `sudo systemctl start perfect-day-backup.service`. Check that `backup-status.json` shows `result=ok`, that `rsync --list-only rsync://192.168.1.112/Backups/perfect-day/nightly/` shows tonight's folder, and that the sizes are in line with ~40 MB of objects and ~160 KB of dump.
4. With OK, run `sudo ./scripts/nuc/drill.sh`. Expected: `DRILL PASSED`, then `docker ps` shows no `perfect-day-drill-*` containers and `docker volume ls` shows no `perfect-day-drill_*` volumes.
5. The next morning, check the 02:17 timer run's `result=ok`. Mark row 36 **done** in a small docs PR, and update the memory.
