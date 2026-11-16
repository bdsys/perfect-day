# NUC Ops Runbook

Operational runbook for deploying, updating, rebuilding, and recovering the Perfect Day stack on the home-lab Intel NUC.

This document is the **runbook** — step-by-step commands you run to take action. For the underlying hardware/edge/storage architecture and design rationale, see [`deploy/nuc.md`](nuc.md). For Cloudflare-specific configuration, see [`deploy/cloudflare.md`](cloudflare.md).

**Audience:** the operator (you) installing or maintaining the NUC. Assumes shell access to the NUC and a workstation with `ssh` and a password manager.

---

## Quick reference

| Situation | Section |
|---|---|
| First-time setup, new NUC | [Phase A](#phase-a--third-party-account-setup) → [Phase B](#phase-b--nuc-deployment) in order |
| Push new code already on `main` | [B-Update — routine update](#b-update--routine-update) |
| State got messy, need clean rebuild | [B3.5 — Full reinstall](#b35--full-reinstall-when-incremental-cleanup-is-not-working) |
| Bad deploy, need to revert | [B-Rollback](#b-rollback) |
| Daily DB backup failing | [B7 — Backups](#b7--backups) |
| Validate prod after deploy | [B8 — Validate production deployment](#b8--validate-production-deployment) |

---

## Dependency map

Several steps unlock others. Do not skip ahead.

```
A1 (Cloudflare account + NS delegation)
  └─ A2 (DNS A records + DDNS token)
       └─ B4 (DDNS updater running)
            └─ B5 (FortiGate Origin Cert install)  ← requires DNS resolving + CF proxy ON
                 └─ B6 (add prod Google OAuth redirect URI)

A3 (Google Cloud project + OAuth creds)
  └─ B2 (secrets on NUC — needs GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET)
       └─ B6 (add prod redirect URI)

A4 (SendGrid API key)
  └─ B2 (secrets on NUC — needs SENDGRID_API_KEY)

A5 (Anthropic API key)
  └─ B2 (secrets on NUC — needs ANTHROPIC_API_KEY)

B1 (NUC bootstrap) → B1.5 (deploy key) → B2 (secrets) → B3 (first deploy)
                                                          → B4 + B5 → B6 → B7 (backups) → B8 (smoke test)
```

---

## Phase A — Third-party account setup

Do these first. They unlock everything in Phase B. All API keys are stored only in your password manager and on the NUC at `/etc/perfect-day/app.env` (mode 600).

### A1 — Cloudflare: hand over `andrewlass.com` and create DNS records

Cloudflare becomes the authoritative DNS for `andrewlass.com`. GoDaddy stays as the registrar (where you pay for the domain) but stops serving DNS.

1. Create a free account at [cloudflare.com](https://cloudflare.com) if you don't have one.
2. In Cloudflare, click **Add a site** → type `andrewlass.com` → choose **Free plan**.
   - Cloudflare will scan your existing GoDaddy DNS records and import them automatically.
   - Review the imported records — keep anything already there (email MX records, etc.).
   - Cloudflare will give you two nameserver hostnames, e.g. `adam.ns.cloudflare.com` and `beth.ns.cloudflare.com`.
3. Log in to GoDaddy → **My Products** → **andrewlass.com** → **DNS** → **Nameservers** → **Change** → **Enter my own nameservers**.
   - Replace GoDaddy's default nameservers with the two Cloudflare nameservers from step 2.
   - Save. GoDaddy no longer serves DNS for this domain.
4. Wait for propagation (usually 5–30 min, up to a few hours). Verify:
   ```bash
   dig NS andrewlass.com
   # Expect: Cloudflare's nameservers
   ```

### A2 — Cloudflare: create app DNS records and DDNS token

1. In Cloudflare → **DNS** for `andrewlass.com`, add three A records:
   ```
   Type: A   Name: perfectday         Value: <YOUR_NUC_WAN_IP>   Proxy: ON   TTL: Auto
   Type: A   Name: perfectday-api     Value: <YOUR_NUC_WAN_IP>   Proxy: ON   TTL: Auto
   Type: A   Name: perfectday-media   Value: <YOUR_NUC_WAN_IP>   Proxy: ON   TTL: Auto
   ```
   To find your NUC WAN IP: `curl https://api.ipify.org` (run from the NUC or any home device).

   Every name has exactly one label before `andrewlass.com`: Cloudflare's free Universal SSL does not cover deeper names (P-9). Proxy must be **ON** (orange cloud): Cloudflare handles the public-facing TLS; FortiGate uses a Cloudflare Origin Certificate for the CF↔origin hop. See [`deploy/cloudflare.md`](cloudflare.md) and [`deploy/nuc.md` § FortiGate Virtual Server setup](nuc.md#fortigate-virtual-server-setup).

2. Create a scoped API token for DDNS updates:
   - Cloudflare → **My Profile** → **API Tokens** → **Create Token**
   - Use template **Edit zone DNS**
   - Under **Zone Resources** → select `andrewlass.com` only
   - **Continue to summary** → **Create Token**
   - **Save this token** — you will not see it again. Store in your password manager.

3. Verify DNS resolves:
   ```bash
   dig +short perfectday.andrewlass.com
   # Expect: your NUC WAN IP (or Cloudflare anycast IPs once Proxy is ON)
   ```

### A3 — Google Cloud: create a project and OAuth credentials

Follow [`docs/local-dev.md` § Google Cloud](../docs/local-dev.md#google-cloud--project-strategy). One project (today `perfect-day-dev-514922`, in Andrew's Google account since 2026-11-16) has two Web clients: `Perfect Day local` and `Perfect Day NUC`. Enable the **Google Calendar API** and the **Google Photos Picker API**, add the scopes and your test users, then download each client's JSON from **Google Auth platform → Clients**. The NUC client's file is what [B-Config](#b-config--change-an-existing-nucs-config) reads. Keep both files in your password manager, not in Downloads.

### A4 — SendGrid: create account and get API key

SendGrid is needed because residential IPs are blocked by most mail servers. It handles email delivery for magic links, password resets, etc. Phase 1 only needs it for the NUC deployment (not local dev).

1. Create a free account at [sendgrid.com](https://sendgrid.com). Free tier allows 100 emails/day, which is plenty for PoC.
2. Verify your email address when prompted.
3. Complete the required sender identity setup:
   - **Settings** → **Sender Authentication**
   - Choose **Single Sender Verification** (easiest for PoC)
   - From Name = `Perfect Day`, From Email = `pd@bdsys.net`, Reply To = your email
   - **Create** → check your email → click the verification link
4. Get an API key:
   - **Settings** → **API Keys** → **Create API Key**
   - Key name: `perfect-day-poc`
   - Permission: **Restricted Access** → enable **Mail Send** → Full Access
   - **Create & View**
   - **Copy the key immediately** — it is only shown once. Save to password manager.
5. (Optional but recommended) Domain authentication for better deliverability:
   - **Settings** → **Sender Authentication** → **Authenticate Your Domain**
   - Domain: `andrewlass.com`, Link branding: off
   - SendGrid will give you DNS records to add in Cloudflare DNS (CNAME records for `s1._domainkey.andrewlass.com` etc.)
   - Add them in Cloudflare, then click **Verify** in SendGrid

### A5 — Anthropic: get API key

1. Go to [console.anthropic.com](https://console.anthropic.com).
2. **API Keys** → **Create Key** → name it `perfect-day-poc`.
3. Copy and store in password manager.
4. Add billing/credits if prompted (pay-as-you-go; PoC usage will be minimal).

---

## Phase B — NUC deployment

### B1 — Bootstrap the NUC server (run once)

```bash
ssh andrew@<NUC_IP>
git clone git@github.com:bdsys/perfect-day.git ~/perfect-day
cd ~/perfect-day
sudo ./scripts/nuc/00-bootstrap.sh
```

Installs Docker, UFW firewall, fail2ban; creates the `perfectday` service user and `/opt/perfect-day/`. Adds `andrew` to the `docker` group (re-login required).

### B1.5 — Set up GitHub deploy key for git clone

```bash
sudo ssh-keygen -t ed25519 -C "perfect-day-nuc-deploy" -f /root/.ssh/id_ed25519 -N ""
sudo cat /root/.ssh/id_ed25519.pub
```

Add the printed public key to GitHub: repo → **Settings** → **Deploy keys** → **Add deploy key** (read-only).

Verify: `sudo ssh -T git@github.com` → should print `Hi bdsys/perfect-day! You've successfully authenticated...`

### B2 — Provision secrets on NUC

```bash
cd ~/perfect-day
sudo ./scripts/nuc/10-secrets.sh
```

The script will prompt for:
- `ANTHROPIC_API_KEY` — from [A5](#a5--anthropic-get-api-key)
- `GOOGLE_CLIENT_ID` — from [A3](#a3--google-cloud-create-a-project-and-oauth-credentials)
- `GOOGLE_CLIENT_SECRET` — from [A3](#a3--google-cloud-create-a-project-and-oauth-credentials)
- `SENDGRID_API_KEY` — from [A4](#a4--sendgrid-create-account-and-get-api-key)
- `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ZONE_ID` — from [A2](#a2--cloudflare-create-app-dns-records-and-ddns-token) (optional, for DDNS sidecar)
- `FORTIGATE_LAN_IP` — the NUC LAN IP that FortiGate forwards traffic to. Used by the Caddy edge `trusted_proxies` directive so client IPs in logs are real client IPs, not the FortiGate. Leave empty for local dev (falls back to RFC1918 `private_ranges`).

Auto-generates all crypto keys. Output: `/etc/perfect-day/app.env` (mode 600).

> **⚠️ Back up `/etc/perfect-day/app.env` to your password manager immediately after this step.** If the disk dies without a backup, all encrypted OAuth tokens become permanently unreadable.

### B3 — First deploy

```bash
cd ~/perfect-day
sudo ./scripts/nuc/20-deploy.sh
```

Clones the repo, renders `Caddyfile` from `Caddyfile.tmpl`, runs migrations, starts all services. If `/readyz` returns 503 afterward:

```bash
cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh
```

If you have re-run `10-secrets.sh` (which regenerates `POSTGRES_PASSWORD`), you must wipe the Postgres volume first — otherwise the new password won't match what the DB was initialized with:

```bash
sudo ./scripts/nuc/20-deploy.sh --clean
```

`--clean` wipes all `perfect-day_*` Docker volumes across both `--profile nuc` and `--profile dev` (so pgadmin containers from stray dev invocations don't block the volume wipe). The wipe runs after `git pull` so compose sees the current YAML before stopping services.

### B3.5 — Full reinstall (when incremental cleanup is not working)

When iterative fixes fail, do a full nuke and rebuild from scratch. This is the preferred approach for any "state got messy" situation — it is deterministic and takes about 5 minutes.

```bash
# 1. Full nuke (stops systemd, all containers, all volumes, removes secrets + repo):
sudo ./scripts/nuc/99-teardown.sh --yes
```

Run without `--yes` first to see a dry-run summary of what will be destroyed.

After teardown you **must** have your four API keys ready:
- `ANTHROPIC_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `SENDGRID_API_KEY`

```bash
# 2. Re-clone + re-provision secrets (will re-prompt for all four API keys + FORTIGATE_LAN_IP):
sudo git clone git@github.com:bdsys/perfect-day.git /opt/perfect-day
cd /opt/perfect-day
sudo ./scripts/nuc/10-secrets.sh

# 3. Deploy (no --clean needed; volumes are already gone):
sudo ./scripts/nuc/20-deploy.sh
```

> **Why this fixes the recurring Postgres auth failure:** there are six independent state sources on the NUC (running containers across two Docker profiles, named volumes, `/etc/perfect-day/app.env`, `/opt/perfect-day` repo, systemd unit). The auth failure happens when `10-secrets.sh` regenerates `POSTGRES_PASSWORD` but the `postgres_data` volume survives — Postgres only reads the password on first init, so the env and on-disk hash drift. `99-teardown.sh` clears all six sources simultaneously so the next deploy starts from a known-clean state.

### B4 — Cloudflare DDNS setup

Your home IP changes occasionally. The `cloudflare-ddns` sidecar container (already in `docker-compose.yml`) keeps the DNS A records current.

**Prerequisites:** API token with `Zone.DNS:Edit` scope and your Zone ID (see [A2](#a2--cloudflare-create-app-dns-records-and-ddns-token) and [`deploy/cloudflare.md`](cloudflare.md) § 2.1).

If you provided the token and zone ID in [B2](#b2--provision-secrets-on-nuc), the DDNS sidecar will start on the next deploy. If you skipped those prompts on a fresh NUC, re-run `10-secrets.sh` before the first deploy. **On a NUC that already has data, never re-run `10-secrets.sh`** (it regenerates `MASTER_SECRET`); write the DDNS config file by hand (see [`deploy/cloudflare.md`](cloudflare.md) § 2.2) and run [B-Config](#b-config--change-an-existing-nucs-config), which also repairs a config file the updater cannot parse.

**Start the updater (if NUC is already deployed):**
```bash
cd /opt/perfect-day
docker compose up -d cloudflare-ddns
```

**Verify:**
```bash
docker compose --profile nuc logs cloudflare-ddns --tail=20
# Expect: updates or "unchanged" for perfectday, perfectday-api, perfectday-media.
# The records are proxied, so `dig` returns Cloudflare's IPs, never the WAN IP. To check the
# origin IP, compare each record's Content in the Cloudflare dashboard with
# `curl -s https://api.ipify.org` on the NUC, and check the orange cloud is still on.
```

### B5 — FortiGate: virtual hosts and TLS certificates

**Requires [A2](#a2--cloudflare-create-app-dns-records-and-ddns-token) (DNS records resolving and Cloudflare proxy ON).**

Follow the procedure in [`deploy/nuc.md` → FortiGate Virtual Server setup](nuc.md#fortigate-virtual-server-setup). It covers:

1. An Origin Certificate for `*.andrewlass.com, andrewlass.com` from a CSR generated on the FortiGate (the one in use expires 2027-11-16)
2. A TCP health check, and a single HTTPS virtual server on WAN:443 with one real server → NUC:80 (Caddy edge)
3. One proxy-mode firewall policy to that virtual server. Restricting it to Cloudflare's IP ranges is still an open item

When complete, verify from off-network:
```bash
curl -sI https://perfectday.andrewlass.com/                 # Expect: the web app (200 or a redirect), not Caddy's "Not Found"
curl -s  https://perfectday-api.andrewlass.com/readyz       # Expect: 200 from FastAPI
dig +short perfectday.andrewlass.com                    # Expect: Cloudflare anycast IPs
```

### B5.5 — Bring up the Caddy edge on the NUC

After [B3](#b3--first-deploy) and [B5](#b5--fortigate-virtual-hosts-and-tls-certificates), start the Caddy edge container if it is not already running:

```bash
cd /opt/perfect-day
docker compose --profile nuc up -d edge
```

Verify Host-header routing from inside the NUC LAN:
```bash
curl -sI -H "Host: perfectday.andrewlass.com" http://<NUC_LAN_IP>:80/
# Expect: the web app (200 or a redirect), not Caddy's "Not Found"

curl -sH "Host: perfectday-api.andrewlass.com" http://<NUC_LAN_IP>:80/healthz
# Expect: {"status":"ok"} from FastAPI
```

See [`deploy/caddy/README.md`](caddy/README.md) for full Caddy edge documentation and local debug workflow.

### B6 — Add production Google OAuth redirect URI

The `Perfect Day NUC` client (Google Auth platform → Clients) must have:

- Authorized JavaScript origin: `https://perfectday.andrewlass.com`
- Authorized redirect URI: `https://perfectday-api.andrewlass.com/v1/integrations/google/callback`

Then put the client into `app.env` with [B-Config](#b-config--change-an-existing-nucs-config).

### B7 — Backups

```bash
cd ~/perfect-day
sudo ./scripts/nuc/30-backup.sh
```

Configure rclone for Backblaze B2 when prompted. Sets up daily encrypted `pg_dump` backups (~$5/mo storage cost).

The `age` private key for decryption lives **off-NUC** (USB key, password manager). Without it, the backup is unreadable even if an attacker gets the bucket. See [`deploy/nuc.md` § Backup](nuc.md#backup) for the architecture and quarterly DR-drill procedure.

### B8 — Validate production deployment

```bash
make test-smoke BASE=https://perfectday-api.andrewlass.com
# Expect: 16 PASS lines
```

Then manually walk through the app at `https://perfectday.andrewlass.com`:
- Register with email+password
- Log in with Google OAuth
- Create a diary, connect Google Calendar
- Trigger a scan, verify a draft entry appears
- Edit and publish the draft

---

## B-Config — change an existing NUC's config

Use this whenever the hostnames or the Google client change. It edits files in place and keeps everything else in `app.env`, including `MASTER_SECRET`. **Do not re-run `10-secrets.sh` on a NUC that has data:** it regenerates `MASTER_SECRET`, and stored photos and Google tokens are encrypted with it.

```bash
# from your PC: copy the NUC client's JSON over (it holds the client secret)
scp pd-gcp-oauth-nuc-client.json andrew@<NUC_IP>:/tmp/
# on the NUC:
cd /opt/perfect-day
sudo ./scripts/nuc/15-update-config.sh --google-client /tmp/pd-gcp-oauth-nuc-client.json
shred -u /tmp/pd-gcp-oauth-nuc-client.json
sudo ./scripts/nuc/40-update.sh      # the web build bakes in the API URL and client ID
```

The script:
1. Sets `CORS_ORIGINS`, `GOOGLE_REDIRECT_URI` and `NEXT_PUBLIC_API_URL` to the current hostnames.
2. With `--google-client`, sets `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `NEXT_PUBLIC_GOOGLE_CLIENT_ID` from the file. It refuses a file whose redirect URI or JavaScript origin is not the NUC's, and changes nothing.
3. Rewrites `cloudflare-ddns.config.json` with the three current names and `proxied: true`, keeping the API token and zone ID (and dropping stray control characters, which stopped the updater on 2026-09).
4. Copies each file it changes to `<file>.bak-<timestamp>` first, and never prints a secret.

Tests: `bash scripts/nuc/tests/update-config.test.sh` (not in CI).

**After a Google client change,** every user's Calendar and Photos connection belongs to the old client, so each user reconnects Google once.

## B-Update — routine update

Builds the app images on the NUC from `main` (or a specific SHA), runs migrations and restarts the app services. Infra (Postgres, Redis, MinIO) stays up. CI-built images pulled on a timer are planned (PoC 25.4, finding P-2).

```bash
ssh andrew@<NUC_IP>
cd /opt/perfect-day
sudo ./scripts/nuc/40-update.sh             # latest main
# or
sudo ./scripts/nuc/40-update.sh abc1234     # specific SHA
```

The script:
1. `git fetch`, then `git checkout main && git pull --ff-only` (or `git checkout <sha>`)
2. Re-renders `Caddyfile` from `Caddyfile.tmpl` using `FORTIGATE_LAN_IP` from `app.env`
3. If the commit differs from the one deployed, tags the running `perfect-day-{api,worker,beat,web}` images `:previous` and records that commit in `previous-deployed-sha`
4. `docker compose build api worker beat web` (the web bakes in `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_GOOGLE_CLIENT_ID` from `app.env`), and pulls the Caddy image
5. `alembic upgrade head` for migrations
6. Restarts the app services; recreates `edge` and `cloudflare-ddns` so they reread their mounted files
7. Polls `/readyz` for up to 90s; on failure tells you to run `50-rollback.sh`
8. Records the new SHA in `/opt/perfect-day/last-deployed-sha`, then runs `./scripts/smoke-test.sh` against the public API (it registers a throwaway user each time)

Update logs land in `/var/log/perfect-day/update-<timestamp>.log`.

## B-Rollback

If `40-update.sh` aborts (or you discover a regression after a deploy):

```bash
sudo ./scripts/nuc/50-rollback.sh
```

It goes back exactly one deploy: it checks out the commit in `previous-deployed-sha`, retags the `:previous` images as `:latest`, recreates the app services and Caddy, and waits for `/readyz`. The next `40-update.sh` returns to `main`. Database migrations are **not** reverted. Check `alembic history` and run `docker compose --profile nuc run --rm api alembic downgrade -1` by hand if the older code needs it.

If rollback also fails, fall back to [B3.5 — Full reinstall](#b35--full-reinstall-when-incremental-cleanup-is-not-working) and restore the latest backup.

---

## Known gaps and flagged issues

See [`design/known-issues.md`](../design/known-issues.md) for the full list of open issues and technical debt.
