# NUC backups to the NAS (tracker row 36)

Status: design, awaiting Andrew's approval (2026-11-18).

## Why

Nothing in production is backed up. Every diary entry and every photo of
Andrew's child exists only on the NUC's disk. A dead disk, a bad deploy or a
mistaken command would lose all of it. The goal is that losing the NUC loses
at most one day of data, and that a restore is a tested, written procedure,
not a hope.

## What Andrew decided (2026-11-18)

- **Destination:** the NAS at `192.168.1.112`, folder `Backups/perfect-day`.
  Andrew copies the whole `Backups` share to an external drive every so often
  and keeps it at his in-laws' house, 30 miles away. That is the offsite copy,
  so no cloud copy (the old Backblaze B2 plan is dropped).
- **Retention:** every night for the last 30 days, plus one per month for the
  last 12 months.
- **Failure alerts:** the log file only, for now. A backup status card in a
  future admin panel is tracker row 62.
- **No password on the NAS's rsync service.** Known limit: anyone on the home
  network can read the backup folder (the files are encrypted, so this exposes
  nothing) and can delete it. The offsite drive is the cover for deletion.

## Goals

1. Every night, a complete, restorable backup of production:
   - the Postgres database;
   - every object in the photos bucket (photos, display copies, avatars);
   - the server's secrets and config (`/etc/perfect-day/`), which hold
     `MASTER_SECRET`. Photos and Google tokens are unreadable without it.
2. Everything is encrypted on the NUC before it leaves.
3. Backups are on the NAS by morning, kept to the retention above.
4. A restore script, and a restore drill run once before this is called done.
   The drill restores a real backup into a throwaway copy of the stack and
   proves it works.
5. Each run leaves a machine-readable result that the admin panel (row 62)
   can show later.

## Non-goals

- Alerts by email or push. The app can't send email yet (row 49).
- A cloud or offsite copy. Andrew's external drive covers it.
- Point-in-time recovery between nightly runs. At most one day's loss is
  acceptable.
- Replacing MinIO. That's row 41, done after this, but see "Why the photos
  go through the S3 interface".

## Anti-goals (a failure even if it "works")

- A backup that can't be restored: wrong key, missing `MASTER_SECRET`, a
  truncated file mistaken for a good one.
- Child photos or diary text readable by anyone who reaches the NAS or the
  external drive.
- A failed or partial run that deletes good older backups.
- A backup job that disturbs production: it locks tables, fills the disk, or
  touches containers other than `perfect-day-*` on the shared NUC.

## Constraints

- The NUC is shared. The job touches only `perfect-day-*` containers and the
  `perfect-day` compose project.
- No inbound connections to the home network. The NUC pushes to the NAS; the
  NAS never connects in.
- The NAS speaks rsync (daemon, port 873, module `Backups`, anonymous), SMB
  and NFS, but not SSH. It looks like an older Iomega/LenovoEMC unit that
  probably no longer gets updates. Encryption before it leaves the NUC is
  therefore required, not optional.
- Data is small today: about 40 MB of photo objects and a 160 KB compressed
  dump (2026-11-18). Full copies each night are fine. Incremental schemes add
  risk and save nothing.
- **Never re-run `10-secrets.sh` on the NUC.** The backup must capture the
  secrets it made, not regenerate them.
- Production changes on the NUC (installing the timer, the restore drill)
  need Andrew's OK at the time.

## Approaches considered (for the photos)

1. **Export through the S3 interface (chosen).** A new admin command,
   `photos export`, runs in a one-off API container like the other
   `admin.sh` commands. It lists every object in the bucket and writes a tar
   of them to stdout. `photos import` reads that tar back into a bucket.
2. **Copy MinIO's data folder** (`/var/lib/docker/volumes/perfect-day_minio_data`).
   Simpler, but it ties the backup to MinIO's internal on-disk format and file
   ownership. Those are exactly what row 41 changes, and a copy taken while
   MinIO writes can be inconsistent.
3. **A sync tool in its own container** (rclone or `mc mirror`). Same result
   as 1, but it's one more image to pin and keep updated, and `minio/mc` is
   itself no longer published.

**Why the photos go through the S3 interface:** the same export and import
are the migration path for row 41. To move to any other S3-compatible
server, start it on an empty volume, import the latest export, check it, and
switch over, with the old volume left untouched for rollback. That avoids
the volume-ownership change the tracker warned about.

## The design

**One run** (nightly at 02:17 by a systemd timer; `systemctl start` runs it by
hand):

1. Take a lock so two runs, or a run and a deploy, never overlap.
2. Dump the database (`pg_dump` inside the postgres container, as the old
   script did), gzip it, and encrypt it with age to `db.sql.gz.age`.
3. Export the photos (`admin.sh photos export`) and encrypt them to
   `photos.tar.age`. The database is dumped first, so every photo row in
   the dump has its object. An object uploaded in between is an orphan,
   which is harmless and already swept.
4. Tar `/etc/perfect-day/` and encrypt it to `config.tar.age`.
5. Write `manifest.json`: date, start and end times, the size of each file,
   its SHA-256, object count and total object bytes, database row counts for
   users, diaries, entries and photos, and the git commit the NUC runs.
6. **Verify before keeping:**
   - decrypt each file;
   - check that the dump ends with pg_dump's completion marker;
   - check that the photo tar lists the same object count the export
     reported;
   - check that the config tar contains `app.env`.

   Files are written as `.partial` and renamed only after this passes.
7. Keep the night's folder locally under `/var/backups/perfect-day/nightly/YYYY-MM-DD/`
   and apply retention locally. Retention keeps the newest 30 nightly
   folders, plus the earliest good folder of each of the last 12 months.
8. Copy the night's folders to `rsync://192.168.1.112/Backups/perfect-day/nightly/`,
   adding and updating only. Then apply the same keep rule to **everything the NAS
   holds**, its own history and the local nights together, and delete only the NAS
   folders that rule drops. A rebuilt machine with a few nights therefore never trims
   the NAS's older backups. *(Changed 2026-11-18 after a side agent spotted that an
   exact mirror would let a rebuilt NUC delete months of NAS history, and with it
   history on the external drive the next time Andrew refreshes it from the NAS.)*
   Nothing on the NAS is deleted on a run that failed before this step.
9. Write the result to `/var/log/perfect-day/backup-status.json` (last
   attempt, last success, sizes, error text on failure) and append a line to
   `/var/log/perfect-day/backup.log`. A failed run exits non-zero, so
   `systemctl status perfect-day-backup` shows it failed.

**Keys:**
- A new age keypair, made once by the install script. The NUC encrypts with
  the public key.
- The private key is in `/etc/perfect-day/backup.key` (root, mode 600), where
  the nightly verify step uses it, and **in Andrew's password manager**.
- Andrew also stores `MASTER_SECRET` there separately, as a second safety
  net.
- With the private key, everything else, `MASTER_SECRET` included, can be
  recovered from any backup.
- The install script prints the private key once and asks Andrew to confirm
  he has saved it before the timer is enabled. The key is never sent through
  chat or any tool.

**What goes to the NAS:** only `nightly/`, mirrored to
`Backups/perfect-day/nightly/`. The ad-hoc dumps taken before big changes
(`/var/backups/perfect-day/pre-*.sql.gz`) are unencrypted, so they stay on the
NUC and are never copied. *(My call.)*

The folder is mode 700, so the ad-hoc dumps were never readable by other
users on the shared NUC. One dump, made 2026-11-18 with `sudo tee`, was
written as `-rw-r--r--` because of the default umask; it's 600 now. The
install script keeps the folder at 700, and the backup job and the runbook's
dump command write under `umask 077`. *(My call.)*

**What counts as success:** a run succeeds only if the backup is made,
verified and copied to the NAS. If the NAS copy fails, the night's local
folder is kept, the run exits non-zero, and `backup-status.json` records the
local and NAS results separately (`last_local_success`, `last_nas_success`).
The next good run copies everything that's missing. *(My call.)*

**Restore** (`scripts/nuc/restore.sh <date> [--from-nas] [--into <project>] [--with-config]`):
- Takes the night's files from the local `nightly/` folder, or with
  `--from-nas` fetches them from `rsync://192.168.1.112/Backups/perfect-day/nightly/<date>/`.
  The NAS is the copy we'd actually rely on.
- Decrypts them with the private key: the file given by `--key`, or
  `/etc/perfect-day/backup.key`.
- Restores the database into the target project's postgres, and imports the
  photos into the target project's bucket.
- Restores `/etc/perfect-day/` only with `--with-config`, because
  overwriting production's live `app.env` is a deliberate act.
- **Into production (no `--into`) it's guarded** *(my call)*:
  - it asks you to type the date to confirm;
  - it takes a fresh ad-hoc dump of the current database first;
  - it stops `api`, `worker` and `beat`;
  - it drops and recreates the database, then restores;
  - it imports photos into the bucket, overwriting same-named objects and
    leaving others in place;
  - it starts the services again and runs the smoke test.

**Losing the NUC (rebuild on a new machine).** Documented in
`deploy/nuc-ops.md` § B7 as a fresh-machine path. *(My call: the plan
includes it; it's the main threat this row exists for.)*
1. Bootstrap the machine (`00-bootstrap.sh`).
2. Put the private key from the password manager in `/etc/perfect-day/backup.key`.
3. Fetch the newest night from the NAS, or the external drive, and restore
   `/etc/perfect-day/` from it **before anything else**. **Do not run
   `10-secrets.sh`:** it would make a new `MASTER_SECRET`, and Postgres only
   reads its password the first time it starts.
4. Deploy (`20-deploy.sh`), which uses the restored `app.env`.
5. Run `restore.sh <date> --from-nas` for the data.

The plan checks that `20-deploy.sh` works with an existing `app.env` and
doesn't regenerate secrets; if it doesn't, that's fixed too.

**Restore drill** (once, before done; then whenever the procedure changes).
The data never leaves the NUC during it.
1. On the NUC, run `restore.sh <newest> --from-nas --into perfect-day-drill`.
   It restores into a throwaway compose project with its own project name and
   volumes, no published ports, and the restored `app.env`.
2. The drill project runs **only postgres, minio and api**. It never starts
   `worker`, `beat`, `edge` or `cloudflare-ddns`, so nothing scans Google,
   calls Anthropic, sends email or touches DNS. *(My call.)*
3. Check:
   - row counts match the manifest;
   - the object count matches;
   - a new `photos verify` admin command, run in the drill project's api
     container, decrypts a sample of photos (every user's avatar and up to
     20 photos) with the restored keys and reports any failure.

   Nobody logs in, and no login token is minted. *(My call.)*
4. Tear the drill project and its volumes down.

Exact commands go in `deploy/nuc-ops.md` § B7.

**Replaces** `scripts/nuc/30-backup.sh` (Postgres to B2, never installed).
Its B2 and rclone parts go; the age approach and the `pg_dump` call stay.

## What's left to the builder

- The tar layout inside `photos.tar` (object key as path is fine), and
  whether content-type travels with it. Today every object is
  `application/octet-stream`, encrypted by the app.
- How retention and the NAS copy are implemented (rsync `--delete` with the
  guard above, or explicit pruning), and the lock mechanism.
- Tests:
  - integration tests for `photos export` and `photos import`, round-trip
    against the test MinIO;
  - script tests in `scripts/nuc/tests/`, following the existing ones, for
    retention selection, the partial-file and verify behaviour, and the
    "never delete after a failed run" guard.
- Disk guard: refuse to start if the backup disk has less than a margin free.

## Verification (definition of done)

1. `make test-all` and the dev-stack check pass. CI is green.
2. On the NUC, with Andrew's OK:
   - install the timer;
   - Andrew confirms the private key and `MASTER_SECRET` are in his password
     manager;
   - run one backup by hand: it shows up in `Backups/perfect-day` on the NAS
     and `backup-status.json` says success.
3. The restore drill passes as described above, restoring from the NAS copy.
4. The next night's timer run succeeds on its own.
5. Tracker row 36 is marked done, and `deploy/nuc-ops.md` § B7 describes the
   backup, the restore and the drill.
