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
   Type: A   Name: diary.perfectday          Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   Type: A   Name: api.diary.perfectday      Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   Type: A   Name: media.diary.perfectday    Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   ```
   To find your NUC WAN IP: `curl https://api.ipify.org` (run from the NUC or any home device).

   Proxy must be **ON** (orange cloud) — Cloudflare handles the public-facing TLS; FortiGate uses a Cloudflare Origin Certificate for the CF↔origin hop. See [`deploy/cloudflare.md`](cloudflare.md) § Cloudflare Origin Certificate setup.

2. Create a scoped API token for DDNS updates:
   - Cloudflare → **My Profile** → **API Tokens** → **Create Token**
   - Use template **Edit zone DNS**
   - Under **Zone Resources** → select `andrewlass.com` only
   - **Continue to summary** → **Create Token**
   - **Save this token** — you will not see it again. Store in your password manager.

3. Verify DNS resolves:
   ```bash
   dig +short diary.perfectday.andrewlass.com
   # Expect: your NUC WAN IP (or Cloudflare anycast IPs once Proxy is ON)
   ```

### A3 — Google Cloud: create a project and OAuth credentials

Google OAuth requires a project in Google Cloud Console. Use a **separate project** for PoC (keep it isolated from any work projects).

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Click the project selector (top bar) → **New Project** → name it `perfect-day-poc` → **Create**.
3. Enable the Google Calendar API:
   - **APIs & Services** → **Library** → search "Google Calendar API" → **Enable**
4. Configure the OAuth consent screen:
   - **APIs & Services** → **OAuth consent screen**
   - User type: **External** (fine for PoC, supports up to 100 test users)
   - App name: `Perfect Day`
   - User support email: your email
   - Developer contact email: your email
   - **Save and continue** through all screens (scopes and test users can be done after)
5. Create OAuth credentials:
   - **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
   - Application type: **Web application**
   - Name: `perfect-day-poc-web`
   - Authorized redirect URIs — add:
     ```
     http://localhost:8000/v1/integrations/google/callback
     ```
     (You will add the production URI in [B6](#b6--add-production-google-oauth-redirect-uri) after FortiGate TLS is set up.)
   - Click **Create**
   - **Download the JSON** and store `client_id` and `client_secret` in your password manager.
6. Add yourself as a test user (required while app is "External" and unverified):
   - **OAuth consent screen** → **Test users** → **Add users** → add your Gmail address

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

If you provided the token and zone ID in [B2](#b2--provision-secrets-on-nuc), the DDNS sidecar will start on the next deploy. If you skipped those prompts, either re-run `10-secrets.sh` or write the DDNS config file manually (see [`deploy/cloudflare.md`](cloudflare.md) § 2.2).

**Start the updater (if NUC is already deployed):**
```bash
cd /opt/perfect-day
docker compose up -d cloudflare-ddns
```

**Verify:**
```bash
WAN_IP=$(curl -s https://api.ipify.org)
DNS_IP=$(dig +short diary.perfectday.andrewlass.com)
[ "$WAN_IP" = "$DNS_IP" ] && echo "OK: DNS matches WAN" || echo "MISMATCH: WAN=$WAN_IP DNS=$DNS_IP"
docker compose logs cloudflare-ddns --tail=20
```

### B5 — FortiGate: virtual hosts and TLS certificates

**Requires [A2](#a2--cloudflare-create-app-dns-records-and-ddns-token) (DNS records resolving and Cloudflare proxy ON).**

Follow the procedure in [`deploy/nuc.md` → FortiGate Virtual Server setup](nuc.md#fortigate-virtual-server-setup). It covers:

1. Generate a CSR on FortiGate and obtain a Cloudflare Origin Certificate (multi-SAN, 15-year validity)
2. Create a single HTTPS Virtual Server on WAN:443 with one realserver → NUC:80 (Caddy edge), and bind an HTTP health-check monitor at the VIP level
3. One firewall policy permitting inbound HTTPS from Cloudflare IP ranges only

When complete, verify from off-network:
```bash
curl -I https://diary.perfectday.andrewlass.com/healthz       # Expect: 200 from Next.js
curl -I https://api.diary.perfectday.andrewlass.com/healthz   # Expect: 200 from FastAPI
dig +short diary.perfectday.andrewlass.com                    # Expect: Cloudflare anycast IPs
```

### B5.5 — Bring up the Caddy edge on the NUC

After [B3](#b3--first-deploy) and [B5](#b5--fortigate-virtual-hosts-and-tls-certificates), start the Caddy edge container if it is not already running:

```bash
cd /opt/perfect-day
docker compose --profile nuc up -d edge
```

Verify Host-header routing from inside the NUC LAN:
```bash
curl -sH "Host: diary.perfectday.andrewlass.com" http://<NUC_LAN_IP>:80/healthz
# Expect: 200 from Next.js

curl -sH "Host: api.diary.perfectday.andrewlass.com" http://<NUC_LAN_IP>:80/healthz
# Expect: {"status":"ok"} from FastAPI
```

See [`deploy/caddy/README.md`](caddy/README.md) for full Caddy edge documentation and local debug workflow.

### B6 — Add production Google OAuth redirect URI

Now that HTTPS is working, add the production callback URL:

1. Google Cloud Console → **APIs & Services** → **Credentials** → your OAuth 2.0 client
2. Under **Authorized redirect URIs**, add:
   ```
   https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback
   ```
3. **Save**

### B7 — Backups

```bash
cd ~/perfect-day
sudo ./scripts/nuc/30-backup.sh
```

Configure rclone for Backblaze B2 when prompted. Sets up daily encrypted `pg_dump` backups (~$5/mo storage cost).

The `age` private key for decryption lives **off-NUC** (USB key, password manager). Without it, the backup is unreadable even if an attacker gets the bucket. See [`deploy/nuc.md` § Backup](nuc.md#backup) for the architecture and quarterly DR-drill procedure.

### B8 — Validate production deployment

```bash
make test-smoke BASE=https://api.diary.perfectday.andrewlass.com
# Expect: 16 PASS lines
```

Then manually walk through the app at `https://diary.perfectday.andrewlass.com`:
- Register with email+password
- Log in with Google OAuth
- Create a diary, connect Google Calendar
- Trigger a scan, verify a draft entry appears
- Edit and publish the draft

---

## B-Update — routine update

Pull new code from `main` (or a specific SHA), run migrations, restart app services. Infra (Postgres, Redis, MinIO) stays up.

```bash
ssh andrew@<NUC_IP>
cd /opt/perfect-day
sudo ./scripts/nuc/40-update.sh             # latest HEAD on main
# or
sudo ./scripts/nuc/40-update.sh abc1234     # specific SHA
```

The script:
1. `git fetch && git pull --ff-only` (or `git checkout <sha>`)
2. Re-renders `Caddyfile` from `Caddyfile.tmpl` using `FORTIGATE_LAN_IP` from `app.env`
3. `docker compose pull` for app images
4. `alembic upgrade head` for migrations
5. `docker compose up -d --no-deps api worker beat web edge`
6. Polls `/readyz` for up to 90s; aborts (and tells you to run `50-rollback.sh`) on failure
7. Records the new SHA to `/opt/perfect-day/last-deployed-sha`
8. Runs `./scripts/smoke-test.sh` against the public API; warns and exits non-zero on failure

Update logs land in `/var/log/perfect-day/update-<timestamp>.log`.

## B-Rollback

If `40-update.sh` aborts (or you discover a regression after a deploy):

```bash
sudo ./scripts/nuc/50-rollback.sh
```

The rollback script reads `/opt/perfect-day/last-deployed-sha` (recorded by the previous successful deploy/update), checks out that SHA, re-pulls images, and restarts app services. Database migrations are **not** automatically reverted — Alembic downgrades are project-specific and are run manually if needed (`docker compose --profile nuc run --rm api alembic downgrade -1`).

If rollback also fails, fall back to [B3.5 — Full reinstall](#b35--full-reinstall-when-incremental-cleanup-is-not-working) and restore the latest backup.

---

## Known gaps and flagged issues

See [`design/known-issues.md`](../design/known-issues.md) for the full list of open issues and technical debt.
