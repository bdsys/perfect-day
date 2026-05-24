# POC Phase 1 — NUC Deployment Guide

Step-by-step guide to deploying Perfect Day Phase 1 on a fresh Intel NUC running Ubuntu 26 LTS. Covers first-time setup, FortiGate edge configuration, backups, and the update + rollback procedures.

Target host: Intel NUC (4-core x86, 8 GB RAM), shared with other household services.
Public hostname: `diary.perfectday.andrewlass.com` (web), `api.diary.perfectday.andrewlass.com` (API).

---

## Prerequisites

### Hardware
- Intel NUC with Ubuntu 26 LTS installed (fresh install recommended)
- Root SSH access from your workstation
- Public IPv4 routed through FortiGate 7.4
- At least 20 GB free disk for Docker images and MinIO data

### On your workstation
| Tool | Why |
|---|---|
| `git` | Clone repo |
| `gh` CLI | Set GitHub repo variables and secrets |
| SSH client | Run NUC bootstrap scripts |
| `docker` (optional) | Local smoke test before deploying |

### API keys you will need
| Key | Where to get |
|---|---|
| `GOOGLE_CLIENT_ID` | [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials |
| `GOOGLE_CLIENT_SECRET` | Same credential in Google Cloud Console |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/) |
| `SENDGRID_API_KEY` | [SendGrid](https://app.sendgrid.com/) — required for email deliverability from residential IP (see `deploy/nuc.md` § Email deliverability) |

---

## 1 — Server Bootstrap

Run once on a fresh Ubuntu 26 LTS install. Installs Docker, configures UFW, creates the `perfectday` service user, and creates the deploy directory.

```bash
# From your workstation, copy the script to the NUC and run it as root:
scp scripts/nuc/00-bootstrap.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/00-bootstrap.sh
```

What `00-bootstrap.sh` does:
1. `apt update && apt upgrade -y`
2. Installs: `docker.io`, `docker-compose-plugin`, `ufw`, `unattended-upgrades`, `fail2ban`, `rclone`, `age`, `openssl`, `curl`, `jq`
3. Enables and starts Docker
4. Creates `perfectday` user, adds to `docker` group
5. Configures UFW: allow 22 (SSH), 80 (HTTP), 443 (HTTPS); deny everything else. **Postgres (5432), Redis (6379), and MinIO (9000/9001) are never exposed to the internet.**
6. Enables `fail2ban` with sshd jail
7. Enables `unattended-upgrades` for security patches
8. Creates `/opt/perfect-day/` and `/var/log/perfect-day/` directories
9. Creates `/etc/perfect-day/` with mode 700, owned root:docker

Bootstrap is idempotent — safe to re-run.

---

## 2 — Secrets Provisioning (Interim)

> **Note — sops+YubiKey TODO:** The design calls for `sops`-encrypted secrets decrypted by a YubiKey-backed age key at process start (see `deploy/nuc.md` § Secrets). That is the target architecture for any multi-user or production deployment. For this PoC, we use a root-owned `.env` file (chmod 600) as an interim measure. The upgrade path is documented at the end of this section.

Run the secrets provisioning script on the NUC (as root via SSH):

```bash
scp scripts/nuc/10-secrets.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/10-secrets.sh
```

The script will prompt you for:
- `ANTHROPIC_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `SENDGRID_API_KEY` (can be left blank; email notifications won't work)

It auto-generates:
- `MASTER_SECRET` (AES-256-GCM key for OAuth token encryption)
- `OAUTH_TOKEN_SECRET` (secondary AES key)
- `SECRET_KEY` (JWT signing key)
- `POSTGRES_PASSWORD` (random 32-byte hex)
- `MINIO_ROOT_PASSWORD` (random 24-byte hex)

Output: `/etc/perfect-day/app.env`, owned `root:docker`, chmod 600.

**Critical:** The script prints a stern reminder — back up the generated file to a password manager or offline device now. If the NUC's disk fails and you have no backup, encrypted OAuth tokens in the database become permanently unreadable (existing users will need to re-authorize Google Calendar).

### Verifying secrets file
```bash
# Must fail for non-root:
ssh perfectday@<NUC_IP> cat /etc/perfect-day/app.env  # → Permission denied

# Must succeed for root:
ssh root@<NUC_IP> cat /etc/perfect-day/app.env
```

### sops+YubiKey upgrade path (TODO for post-PoC)

When you're ready to move off the interim `.env`:

1. Provision a YubiKey with a GPG key that has an age subkey.
2. `sops --age $(age-plugin-yubikey --identity) --encrypt /etc/perfect-day/app.env > secrets/production.enc.yaml`
3. Check `secrets/production.enc.yaml` into the repo (it's ciphertext — safe to commit).
4. Update `docker-compose.yml` to run `sops -d secrets/production.enc.yaml > /run/secrets/app.env` as a pre-start entrypoint command.
5. Store the YubiKey privately; document the decryption procedure in a physical runbook.
6. Delete `/etc/perfect-day/app.env`.

---

## 3 — First Deploy

```bash
# On your workstation:
./scripts/nuc/20-deploy.sh root@<NUC_IP>
```

What `20-deploy.sh` does:
1. Clones the repo into `/opt/perfect-day` (or `git pull` if it already exists)
2. Symlinks `/etc/perfect-day/app.env` → `/opt/perfect-day/.env`
3. `docker compose pull` — pulls all images from GHCR (or builds locally if GHCR is not yet populated)
4. `docker compose run --rm api alembic upgrade head` — applies all Alembic migrations
5. `docker compose up -d` — starts all 7 services
6. Calls `scripts/wait-for-healthy.sh https://api.diary.perfectday.andrewlass.com/readyz 90`
7. Logs the deployed commit SHA to `/opt/perfect-day/last-deployed-sha`

On success, the script prints the deployed SHA and `✓ Deploy complete`.

### First-run note: MinIO bucket

On first deploy, the `photos` bucket may not exist yet if the compose healthcheck hasn't had time to run the bucket init. If `/readyz` returns 503, run:

```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh"
```

---

## 4 — FortiGate Edge Configuration

The FortiGate handles TLS termination and virtual hosting. This is a manual checklist — FortiGate config is applied via the web UI or CLI on the FortiGate device itself, not by these scripts.

### Virtual hosts to create

| Vhost | Backend | Port | Notes |
|---|---|---|---|
| `diary.perfectday.andrewlass.com` | NUC IP | 3000 | Next.js SSR — all methods |
| `api.diary.perfectday.andrewlass.com` | NUC IP | 8000 | FastAPI — all methods |

> Phase 1 does **not** need the `media.diary.perfectday.andrewlass.com` vhost. Photo serving is deferred to Phase 2 per `design/09-poc-scope.md:43-44`. Do not create it now.

### TLS
- Use Let's Encrypt via FortiGate's built-in ACME client, or upload a cert from Certbot.
- Both vhosts need valid TLS — Google OAuth callback URLs must be HTTPS.

### Google OAuth callback URL
In [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → your OAuth 2.0 Client:

- Authorized redirect URIs: `https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback`

This must match exactly what the API uses. If the domain or path is wrong, OAuth will return a `redirect_uri_mismatch` error.

### CORS
`CORS_ORIGINS` in `/etc/perfect-day/app.env` must include `https://diary.perfectday.andrewlass.com`. The bootstrap script sets this automatically. If you add a new origin later, update the env file and restart the API:
```bash
ssh root@<NUC_IP> "cd /opt/perfect-day && docker compose restart api"
```

---

## 5 — Backup Setup

Run the backup provisioning script once after first deploy:

```bash
scp scripts/nuc/30-backup.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/30-backup.sh
```

What `30-backup.sh` does:
1. Generates an `age` keypair at `/etc/perfect-day/backup.key` (private) and `/etc/perfect-day/backup.pub` (public). Mode 600 on private key.
2. Prompts you to configure `rclone` for Backblaze B2 (or you can do this manually: `rclone config`).
3. Installs a systemd timer (`perfect-day-backup.timer`) that runs daily at 02:17 (off-peak, off-the-:00 mark).
4. The timer runs `perfect-day-backup.service`, which:
   - `pg_dump | gzip | age --recipients-file /etc/perfect-day/backup.pub > /var/backups/perfect-day/backup-$(date +%F).sql.gz.age`
   - `rclone sync /var/backups/perfect-day/ b2:perfect-day-backups/`
   - Keeps the 7 most recent local backups; older ones are deleted.

**Critical:** The private key at `/etc/perfect-day/backup.key` is the only way to decrypt the backup. Copy it to a password manager or offline device now. Without it, the backup is unreadable.

### Verify backup is working
```bash
# Check timer status:
ssh root@<NUC_IP> systemctl status perfect-day-backup.timer

# Run a manual backup to verify:
ssh root@<NUC_IP> systemctl start perfect-day-backup.service
ssh root@<NUC_IP> ls /var/backups/perfect-day/

# Verify rclone upload:
ssh root@<NUC_IP> rclone ls b2:perfect-day-backups
```

### Quarterly DR drill
Once per quarter, restore to a clean stack on a separate machine:
1. Download latest `backup-YYYY-MM-DD.sql.gz.age` from B2.
2. Decrypt: `age -d -i backup.key backup-YYYY-MM-DD.sql.gz.age | gunzip > restored.sql`
3. Spin up a clean `docker compose up -d postgres redis minio`
4. `psql -h localhost -U perfectday perfectday < restored.sql`
5. `docker compose up -d api worker beat web`
6. Confirm diary timeline, entries, and login all work.

---

## 6 — Post-Deploy Validation

Run the smoke test from your workstation (off-NUC) to confirm public reachability:

```bash
./scripts/smoke-test.sh https://api.diary.perfectday.andrewlass.com
```

This exercises every Phase 1 API endpoint and exits non-zero on any failure. Expected output: 16 `PASS` lines and `All 16 checks passed`.

### Browser golden path
1. Navigate to `https://diary.perfectday.andrewlass.com`
2. Register a new account with email + password
3. Create a diary
4. Click "Connect Google Calendar" → complete the OAuth flow in the popup
5. Wait up to 60 seconds for the first scan (or click "Scan now")
6. A draft entry should appear in the diary timeline
7. Click the entry → read the draft → edit if needed → click Publish
8. Confirm the entry shows a "published" badge

---

## 7 — Activating CD (GitHub Actions → NUC)

The `deploy.yml` workflow is checked in but **disabled by default**. It does nothing until `DEPLOY_ENABLED` is set to `true`.

### Required secrets and variables

```bash
# 1. Generate a deploy SSH keypair (do NOT reuse your personal key):
ssh-keygen -t ed25519 -C "perfect-day-deploy" -f ~/.ssh/pd_deploy -N ""

# 2. Add the public key to the NUC's authorized_keys for the perfectday user:
ssh-copy-id -i ~/.ssh/pd_deploy.pub perfectday@<NUC_IP>

# 3. Add secrets to the GitHub repo:
gh secret set GHCR_TOKEN         # GitHub PAT with write:packages scope
gh secret set NUC_SSH_PRIVATE_KEY < ~/.ssh/pd_deploy
gh secret set NUC_HOST --body "<NUC_IP>"
gh secret set NUC_USER --body "perfectday"

# 4. Enable deployments:
gh variable set DEPLOY_ENABLED --body "true"
```

After step 4, any push to `main` will build and push images to GHCR, SSH into the NUC, pull new images, run migrations, restart services, and run the smoke test.

### Verifying CD is enabled
```bash
gh variable list  # should show DEPLOY_ENABLED=true
```

---

## 8 — Updating to a New Release

```bash
# On your workstation (targets HEAD of main by default):
./scripts/nuc/40-update.sh perfectday@<NUC_IP>

# Or target a specific SHA:
./scripts/nuc/40-update.sh perfectday@<NUC_IP> abc1234
```

What `40-update.sh` does:
1. SSHs into the NUC
2. `git pull` in `/opt/perfect-day`
3. `docker compose pull api worker beat web`
4. `docker compose run --rm api alembic upgrade head` (forward-only; aborts on non-zero exit)
5. `docker compose up -d --no-deps api worker beat web`
6. `scripts/wait-for-healthy.sh` — waits up to 90s for `/readyz`
7. Records new SHA to `last-deployed-sha`
8. Runs smoke test; if it fails, prints rollback instructions

---

## 9 — Rollback

```bash
# Roll back to the previously deployed SHA:
./scripts/nuc/50-rollback.sh perfectday@<NUC_IP>

# Or roll back to a specific SHA:
./scripts/nuc/50-rollback.sh perfectday@<NUC_IP> abc1234
```

What `50-rollback.sh` does:
1. Reads previous SHA from `last-deployed-sha` (or uses the argument)
2. Updates compose image tags to `sha-{previous_sha}`
3. `docker compose up -d --no-deps api worker beat web`
4. Waits for `/readyz`
5. Runs smoke test

**Database rollback is manual.** If the new release ran a forward migration, rolling back the application code without rolling back the migration leaves the database schema ahead of the code. In most cases this is harmless (new nullable columns ignored by old code). If a breaking schema change was deployed:

```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && docker compose run --rm api alembic downgrade -1"
```

Only run `downgrade` after confirming with `alembic history` which revision you're rolling back.

---

## 10 — Single Point of Failure Mitigations

The NUC is a single machine — there is no HA story for Phase 1. Known SPoFs and their mitigations:

| Risk | Mitigation | Status |
|---|---|---|
| Power outage | UPS on the NUC | Manual — operator action |
| ISP outage | None (residential) | Accepted for PoC |
| Disk failure | Daily off-site backup to Backblaze B2 | Automated via backup script |
| OS/Docker crash | `restart: always` on all compose services | In `docker-compose.yml` |
| Accidental reboot | `restart: always` + compose auto-start via systemd | Configured by bootstrap script |

If availability becomes a hard requirement, see `deploy/hybrid.md` for the NUC + Hetzner CX21 hybrid topology (Phase 1.5 / Phase 2 deployment switch).

---

## Troubleshooting

**`/readyz` returns 503 after first deploy**
MinIO `photos` bucket doesn't exist. Run:
```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh"
```

**OAuth callback URL mismatch**
The error `redirect_uri_mismatch` means the URL registered in Google Cloud Console doesn't match what the API is sending. Verify that `https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback` is in the OAuth client's authorized redirect URIs.

**Token decryption failure after rotating secrets**
If `MASTER_SECRET` or `OAUTH_TOKEN_SECRET` is changed after data exists in the database, encrypted OAuth tokens become unreadable. Users will see errors when trying to use Google Calendar. They must revoke and re-authorize. To avoid this, never rotate these secrets unless the database is empty or you have a key-rotation migration in place.

**Alembic migration drift (`alembic current` shows wrong revision)**
If migrations were applied manually or out-of-order:
```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && docker compose run --rm api alembic history"
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && docker compose run --rm api alembic stamp head"
```
Only use `alembic stamp` if you are certain the database schema matches `head` and the revision metadata is simply wrong.

**Disk full — Docker layer accumulation**
```bash
ssh perfectday@<NUC_IP> docker system prune -f
ssh perfectday@<NUC_IP> docker image prune -a -f  # removes all untagged images
```
Warning: `image prune -a` removes images not currently running. Run `docker compose up -d` immediately after to re-pull needed images.

**`docker compose up web` fails — port 3000 already in use**
Another process on the NUC is using port 3000. Find and stop it:
```bash
ssh root@<NUC_IP> "lsof -ti:3000 | xargs kill -9"
```

**Celery worker shows `ConnectionRefusedError` for Redis**
Redis is not healthy. Check:
```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && docker compose ps redis"
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && docker compose restart redis"
```
