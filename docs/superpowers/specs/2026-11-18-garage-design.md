# Replace MinIO with Garage — design (tracker row 41, folds in rows 42 and 43)

Status: draft for Andrew's review, 2026-11-18. Branch `feat/garage`.

## Why

MinIO stopped publishing its community images and stopped maintaining the community edition. The NUC runs a `minio/minio:latest` image that will never be updated again, and a machine without a cached copy cannot even pull it. Andrew wants storage that will still be maintained for years, "so we aren't in this situation again in a year". After a comparison of Garage, Silo (the MinIO fork) and SeaweedFS on who maintains each and how (tracker row 41), he chose **Garage**: run by a non-profit (Deuxfleurs), work spread across a team, releases every 1–2 months since 2020, AGPL-3.0.

## Goals

- Production (the NUC), the dev stack, the test suite and CI all store photos in Garage. No MinIO image is needed anywhere afterwards.
- Every photo moves across with nothing lost, and the move is checked before the app uses Garage.
- Going back to MinIO is one command and loses nothing, until Andrew decides MinIO is no longer needed.
- The app's own code does not care which S3 server it talks to. Pointing it somewhere else later (another server, or AWS S3) is a config change, not a code change.
- One pinned Garage version, kept in one place, so an upgrade is one reviewed change.

## Non-goals

- A storage web console. Garage has none; storage stats go into the row 62 admin panel (Andrew added that note).
- Redundancy. One node, one disk, as with MinIO today. The nightly encrypted NAS backup (row 36) is the protection.
- Deleting the old MinIO data. It stays on the NUC, untouched, until Andrew says otherwise (a new tracker row).
- Changing how photos are encrypted. The app encrypts every object before storing it (`services/photo_ingest.py`), so Garage only ever holds ciphertext and the "encrypted at rest" rule holds without server-side encryption.

## Anti-goals (a failure even if it "works")

- Any photo, thumbnail or avatar missing or unreadable after the switch.
- A deleted photo surviving in either store after a switch in either direction. Deletion must cascade (CLAUDE.md, photos of a child).
- A secret printed to a terminal or log, or committed.
- The nightly backup running in the middle of the switch.
- Touching any container on the NUC that is not `perfect-day-*`.
- A MinIO-era assumption left in a script, which breaks the next drill or restore.

## What Andrew sees

- Nothing changes when the first release deploys: the app keeps using MinIO.
- At a time Andrew picks, the switch takes the whole site down for **about 5 minutes** (Andrew OK'd this, 2026-11-18). With 55 objects (40 MB) the copy itself takes seconds.
- Afterwards everything works as before. `/readyz` reports storage as `storage` instead of `minio`.

## Facts this design rests on (checked 2026-11-18)

From a throwaway trial on the dev PC with `dxflrs/garage:v2.5.0`:

- The image runs as root, is 97 MB, and idles at about 11 MiB of memory. It has no shell.
- `garage server --single-node --default-bucket` with `GARAGE_DEFAULT_ACCESS_KEY`, `GARAGE_DEFAULT_SECRET_KEY` and `GARAGE_DEFAULT_BUCKET` creates the key and bucket on first start and is a no-op on later starts.
- A plain key name such as `perfectday` is accepted (not only `GK…`). **Secrets must be at least 16 characters.** Production's current secret is 48.
- **If the secret in the environment differs from the one stored, Garage refuses to start**, with "Access key perfectday is associated with a secret key different than the one given in GARAGE_DEFAULT_SECRET_KEY". Changing the key later is a deliberate step.
- The health check `["CMD", "/garage", "status"]` works without a shell (exit 0 when healthy).
- All 8 S3 calls the app makes work: PutObject, GetObject, HeadObject, DeleteObject, DeleteObjects, ListObjectsV2 (paged), HeadBucket and CreateBucket, including 404s for missing objects and buckets. `export_bucket_tar` → `import_bucket_tar` gave a byte-identical copy.
- The whole integration suite passed against Garage through a generic testcontainers `DockerContainer`: 751 passed, 3 skipped (live-LLM tests).

From Garage's configuration reference (garagehq.deuxfleurs.fr, read 2026-11-18):

- `rpc_secret` can come from the environment as `GARAGE_RPC_SECRET`, so the config file holds no secrets and can be committed.
- `db_engine`: LMDB, the default, "is prone to database corruption after an unclean shutdown (e.g. a process kill or a power outage)". SQLite is described as "more robust". `metadata_fsync` and `data_fsync` reduce loss on power failure at a write-speed cost.
- `replication_factor = 1` is allowed, but the docs say "Do not use this for anything else than test deployments". The meaning is no redundancy: one disk failure loses what is on it. That is the same exposure as single-drive MinIO today, and the NAS backup covers it. Single-node mode is an official feature (`--single-node`).
- `compression_level` defaults to zstd level 1. Our objects are already encrypted and will not compress.

From the code:

- `docker-compose.yml` hard-codes `S3_ENDPOINT_URL: http://minio:9000` for api, worker and beat, overriding `app.env`. On the NUC, `app.env` already says `S3_ENDPOINT_URL=http://minio:9000` (from `10-secrets.sh`), and `./.env` is a symlink to `app.env`, so compose can interpolate it.
- `40-update.sh` restarts only `api worker beat web` (and edge and DDNS) with `--no-deps`. **A new `garage` service is not started by a deploy.**
- `10-secrets.sh` keeps the generated secrets it finds (`keep_or_generate`), but it rewrites the whole `app.env` from a heredoc. So a re-run reverts everything else added since: hostnames, the Google client, and anything this work adds. It still must not be re-run on a NUC with data. CLAUDE.md's reason for that ("it regenerates `MASTER_SECRET`") is out of date; correct it.
- `scripts/smoke-test.sh` never touches photos, and `/readyz` returns 200 even when storage fails (storage is "warning-level" in `routers/health.py`). **A passing smoke test says nothing about storage.**
- The nightly backup does not wait for the deploy lock: if it is held at 02:17 it exits 75 and that night has no backup (`backup-run.sh`).
- `drill.sh` requires the restored `app.env` to be byte-identical to the live one (`cmp -s`).
- `backup-run.sh` exports photos through the api container, so it follows whatever endpoint the app uses. `drill.sh` and `restore.sh --into` start `postgres minio` by name, and `drill.sh` checks that exactly "minio postgres" ran.
- Nothing reads the `minio` key of `/readyz`. Nothing uses presigned URLs, so `S3_PUBLIC_ENDPOINT_URL` is dead (row 43).

## Approaches considered

- **A. Side by side, then flip (chosen by Andrew, 2026-11-18).** Release 1 adds Garage next to MinIO and moves the storage address into config. A one-time switch script stops the app, copies everything into Garage, checks it, points the app at Garage and restarts. `--back` reverses it with nothing lost. Release 2 removes MinIO from compose. Cost: two releases, and MinIO stays defined a little longer.
- **B. Swap in one release.** The deploy replaces MinIO with Garage and copies during the update; rollback is `50-rollback.sh`. Rejected: photos added after the switch are lost on rollback, and the copy has to start a MinIO the compose file no longer defines.
- **C. Through the backups.** A fresh backup, a Garage-only release, then the photos loaded from the backup. Rejected: the same rollback gap as B, with an extra trip through the disk.

## The design

### Garage service (all environments)

- Image `dxflrs/garage:v2.5.0`, pinned. Compose is the one place the tag lives. Anything else that needs the image (the test fixture) reads it from there rather than repeating it.
- Command `/garage server --single-node --default-bucket`. Env:
  - `GARAGE_DEFAULT_ACCESS_KEY: ${S3_ACCESS_KEY}`
  - `GARAGE_DEFAULT_SECRET_KEY: ${S3_SECRET_KEY}`
  - `GARAGE_DEFAULT_BUCKET: ${S3_BUCKET_PHOTOS}`
  - `GARAGE_RPC_SECRET: ${GARAGE_RPC_SECRET}`

  Each has a dev default of at least 16 characters where it needs one.
- **One source for the storage settings in every environment.** api, worker and beat also get `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_BUCKET_PHOTOS` through compose interpolation, with the same defaults as Garage, overriding `apps/api/.env`. Today the app reads `apps/api/.env` while compose interpolates from `./.env` or the shell. Those are the same file only on the NUC (both are symlinks to `app.env`), so without this, dev and CI could hand Garage and the app different keys. Host-run tools (uvicorn on the host, `make infra`) still use `apps/api/.env`, whose example moves to Garage values.
- Config `deploy/garage/garage.toml`, committed and mounted read-only at `/etc/garage.toml`. It holds no secrets:
  - `db_engine = "sqlite"`, `metadata_fsync = true`, `data_fsync = true`;
  - `compression_level = "none"`;
  - `replication_factor = 1`;
  - `rpc_bind_addr` and an explicit `rpc_public_addr` on 3901;
  - `[s3_api]` with `s3_region = "us-east-1"` (the app's current region, so the app does not change) and `api_bind_addr` on 3900.
  - No `[admin]` section yet (row 62 adds it with a token).
- Volume `garage_data` at `/var/lib/garage` (`meta/` and `data/` inside).
- Health check `["CMD", "/garage", "status"]`.
- Ports: none published on the NUC. Dev publishes `127.0.0.1:3900` for host-run tools.

### The storage address becomes config

- api, worker and beat take `S3_ENDPOINT_URL` from interpolation, `${S3_ENDPOINT_URL:-http://garage:3900}`, instead of a hard-coded value.
  - On the NUC, `app.env` says `minio:9000`, so release 1 keeps using MinIO. The switch changes that one line.
  - A fresh dev clone, the test overlay and CI get Garage.
- The api's `depends_on` on the storage service goes. A dependency on Garage before the switch would let a plain `docker compose up` block the app on a store it does not use yet. `/readyz` already treats storage as a warning, not a failure.
- `/readyz` names the check `storage`.

### New settings on an existing NUC

- `GARAGE_RPC_SECRET` must exist in `app.env` before Garage can start. `10-secrets.sh` cannot add it.
- Add one shared helper to `scripts/nuc/lib/` that adds a generated setting to `app.env` only if it is missing. It keeps the file's mode and owner, never prints the value, and backs up first, as `15-update-config.sh` does.
- `10-secrets.sh` (fresh installs) and the switch script both use it.
- `10-secrets.sh` keeps `GARAGE_RPC_SECRET` and `S3_ENDPOINT_URL` when it finds them, as it already keeps the other generated secrets. Only when there is no `S3_ENDPOINT_URL` (a fresh install) does it write `http://garage:3900`. A re-run can then never point an existing install at an empty store. It stops writing `MINIO_*` once release 2 lands.
- This is the long-term mechanism for any future generated setting, not a one-off.

### The switch script: `scripts/nuc/60-switch-storage.sh` (my call on the name)

`sudo ./scripts/nuc/60-switch-storage.sh` moves MinIO → Garage. `--back` moves Garage → MinIO. It runs on the NUC and on the dev stack (the rehearsal), with the compose invocation as the only difference.

1. Take the deploy lock (`lib/deploy-lock.sh`, shared with `40-update.sh` and the nightly backup), so neither runs during the switch.
2. Refuse to run if:
   - the app already points where it is asked to go;
   - the source store isn't healthy;
   - **the source is missing any object the database refers to.** That means every photo's original, thumbnail and display key, and every avatar key, checked in full, not as a sample.

   The last check is what stops an exact copy from wiping the store we would fall back to. Without it, an app wrongly pointed at an empty Garage followed by `--back` would copy "nothing" over MinIO and delete every photo, and every later check would pass, because both stores would be identically empty.
3. Ensure `GARAGE_RPC_SECRET` exists (the helper above), then start the destination service with `up -d --wait`.
4. Dump the database to `/var/backups/perfect-day/` with `umask 077` (standing rule before a big change).
5. Stop api, worker and beat. Web shows the API as unavailable; that is the 5 minutes.
6. Copy: `manage.py photos export` from the source piped into `manage.py photos import` into the destination, in one-off api containers.
   - The export reads MinIO with MinIO's root credentials. The import writes Garage with the app's S3 credentials. On the NUC both are the same values.
   - **The destination must end as an exact copy of the source.** Objects in the destination that the source lacks are deleted, so a photo deleted while on Garage does not come back, or linger, after `--back`. Thanks to the refusal in step 2, those deletions can only ever be objects the database no longer refers to. This needs an exact-copy mode on the import (my call; the builder picks the mechanism).
7. Check before switching:
   - the export and import object counts and byte totals match;
   - the destination's key listing equals the source's;
   - `manage.py photos verify` against the destination passes (a decrypt sample from database rows).

   Any mismatch stops here: the app is started again on the **source**, unchanged, and the script says what differed.
8. Flip `S3_ENDPOINT_URL` in the env file. Resolve the symlink first: on the NUC `./.env` and `apps/api/.env` are symlinks to `app.env`, and `sed -i` on a symlink would replace it with a plain file.
9. Start api, worker and beat. Then check storage through the running app; the smoke test alone can't see it:
   - `/readyz` must report `storage: ok`;
   - `manage.py photos verify` must pass with the app's live settings;
   - then the smoke test runs.

   If any of these fails: flip back, restart on the source, and say so.
10. Stop (not remove) the source service. This proves nothing still uses it. `--back` starts it again.
11. Release the lock and run a backup straight away (`backup-run.sh`), so the first backup from the new store exists before Andrew walks away. Report its result.
12. Print one plain summary: objects and bytes copied, check results, the backup result, where the dump is, and the `--back` command.

The script warns, and asks for confirmation, if started within 30 minutes of 02:17. A nightly backup that meets the lock skips that night rather than waiting.

Every exit path either leaves the app running on the source, unchanged, or running on the destination after all checks passed. An EXIT trap restarts the app if the script dies between steps 5 and 9.

### Drill and restore

- `drill.sh` and `restore.sh --into` start `postgres` plus the storage service named by the host in `S3_ENDPOINT_URL` (`garage` or `minio`), read from the env file. The drill's "only these ran" check uses the same name.
- This works before the switch, after it, and after release 2, and it can never name a service that isn't the app's.
- The production restore path imports through the api, so it already follows the endpoint.
- **Every backup night stays restorable, including those taken before the switch.** Retention keeps monthly nights for a year, so pre-switch nights will be around until late 2027. The photos tar does not depend on the server, so a MinIO-era night loads into Garage unchanged. Only the `S3_ENDPOINT_URL` line in a restored `app.env` is MinIO-era. Therefore:
  - `restore.sh --with-config` keeps the live `S3_ENDPOINT_URL` line rather than the backed-up one. The endpoint describes this deployment, not a secret.
  - On a new machine (`--config-only`, no live file), if the restored line names a service the compose file doesn't define, `restore.sh` sets it to `http://garage:3900` and says so.
  - The drill compares `app.env` with that one line left out.

### Dev, tests and CI (release 1)

- **Test suite:** `tests/integration/conftest.py` replaces testcontainers' `MinioContainer` with a generic `DockerContainer` running Garage. It uses the committed `garage.toml`, `--single-node --default-access-key`, and waits for "S3 API server listening". The fixture names stop saying `minio`. The `minio` extra goes from `testcontainers[...]` in `pyproject.toml`, with the lock updated.
- **Test overlay** (`docker-compose.test.yml`) and the **e2e workflow**:
  - Garage replaces MinIO;
  - `--default-bucket` makes the photos bucket, so the `mc` seed steps go;
  - the `localhost:9000` publish (kept for presigned URLs that no longer exist) goes, unless the builder finds a host-side use;
  - CI's test `.env` uses Garage values.
- **Makefile:**
  - `test-e2e` loses its `minio/mc` step;
  - `seed-bucket` and `scripts/seed-minio-bucket.sh` are deleted (row 42 / P-11);
  - `infra` starts `postgres redis garage`.
- **`bootstrap-local.sh`** waits with `docker compose up --wait` instead of MinIO's HTTP health URL.
- **`20-deploy.sh`** (fresh NUC installs) drops its `seed-minio-bucket.sh` step. That call ends in `|| true`, so it would otherwise fail silently. It starts Garage with everything else.
- **The local dev stack starts fresh** (Andrew, 2026-11-18: "they don't matter, start fresh"). Its volumes (database, MinIO photos, pgAdmin) were already deleted on 2026-11-18 at his request. There is no dev migration.
- **`make nuke-dev`** (Andrew asked for an easy way to wipe dev). It removes the local dev stack's containers and volumes and the e2e scratch folders `/tmp/pd-test-*`, so the next `make up` and `make migrate` start empty. Images are never removed. Guards:
  - it refuses if `/etc/perfect-day/` exists (a NUC) or Docker points anywhere but this machine (`DOCKER_HOST` set, or a non-default context);
  - it lists exactly what it will delete;
  - it asks you to type `nuke`.

  It lives in the Makefile, not `scripts/admin.sh` (my call): `admin.sh` is the production tool on the NUC, and a wipe command there would be one typo away from production.
- **Dev defaults:** `config.py` and `.env.example` defaults move to Garage (`http://localhost:3900` for host-run, keys of at least 16 characters).
- **Row 43:** `s3_public_endpoint_url` and `S3_PUBLIC_ENDPOINT_URL` are removed everywhere.
- **Dependabot:** add the `docker-compose` ecosystem so a new Garage release arrives as a PR against the pinned tag. If Dependabot can't parse it, say so and fall back to a tracker note; the builder verifies.

### Release 2 (after Andrew is happy)

- Remove the `minio` service and `minio_data` declaration from compose, the dev overlay and docs. `MINIO_*` lines in `app.env` become unused; leave them, since removing them is cosmetic and touches a live secrets file.
- **The `perfect-day_minio_data` volume and the `minio/minio` image stay on the NUC.** Undeclaring a volume doesn't delete it. Never prune images.
- `--back` is no longer offered. From here, going back means a restore from backup.
- `20-deploy.sh --clean` wipes `perfect-day_*` volumes. Check it says so loudly enough that it never runs by accident on a NUC holding the old MinIO volume.
- A new tracker row: "delete the old MinIO volume", for Andrew to decide.

When release 2 ships is Andrew's call. My proposal: after at least two nightly backups and one drill have passed from Garage.

### Docs

Update the current-state docs:
- CLAUDE.md (tech stack, photo storage, NUC operations);
- `deploy/nuc-ops.md` (a new section for the switch and `--back`, and B7's drill text);
- `deploy/nuc.md`, `docs/local-dev.md`, `PORTS.md` (3900 replaces 9000/9001), `README.md`;
- `design/` pages that describe the current stack.

Historical plans and specs stay as written. Tracker:
- row 41 done after release 2;
- rows 42 and 43 done with release 1;
- new rows for release 2 and for deleting the old volume.

## Verification

1. `make test-all` green: unit, integration (against Garage), web, e2e (against Garage), script tests.
2. Script tests (`scripts/nuc/tests/`, run by `make test-scripts`) for:
   - the switch script's refusals;
   - the env flip through a symlink, with mode kept;
   - the mismatch path leaving the app on the source;
   - the EXIT trap;
   - `--back`;
   - the helper never printing a value;
   - drill and restore picking the service from the endpoint.
3. An exact-copy test for the import: objects only in the destination are removed.
4. Script tests for `make nuke-dev`: it refuses with `/etc/perfect-day/` present, with `DOCKER_HOST` set, and without the typed word, and it never removes an image.
5. **Rehearsal on this PC, shaped like the NUC.** Run it in its own compose project (`perfect-day-rehearsal`), so Andrew's dev stack is untouched. Give its MinIO a 48-character secret and one env file symlinked into both places, exactly as on the NUC. Dev MinIO's `minioadmin` secret is too short for Garage, so the dev stack can't follow the NUC's path. Steps:
   1. Upload photos through the running app.
   2. Switch, then check photos display and `/readyz` says `storage: ok`.
   3. Delete a photo, run `--back`, and check the deleted photo is gone from MinIO and the others display.
   4. Point the app at an empty store and run `--back`: it must be refused with nothing deleted.
   5. Switch again, then restore a backup night taken before the switch, and check it restores into Garage.
6. The dev-stack check (`make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`), after test-all, never concurrently.
7. CI green, including the labelled e2e run.
8. **On the NUC, each step with Andrew's OK:**
   1. deploy release 1 and confirm nothing changed (`/readyz`, a photo opens);
   2. run the switch at the time Andrew picks;
   3. Andrew checks photos on his phone;
   4. the next 02:17 backup shows `result: ok`;
   5. a drill passes against Garage.

## Left to the builder

- How the import's exact-copy mode works (a flag on `manage.py photos import`, or a separate step).
- How the test fixture reads the image tag from compose.
- Whether the test overlay needs any host port for storage.
- Exact dev default key values.
- Whether `docker-compose` is a supported Dependabot ecosystem for this layout.
