# POC Phase 1 — Master Todo

Where things stand as of 2026-05-24 and what to do next.

---

## Current State

- PR #3 (`poc-p1`) has been **merged to `main`**. ✅
- Backend API is **complete** for Phase 1: auth, diary/entry CRUD, Google OAuth, scan worker, hard-delete flows, rate limiting.
- Local environment is **set up and validated** — unit + integration tests pass, smoke test clean. ✅
- Web UI has real pages (login, register, diary list, diary timeline, entry detail) but **not yet tested end-to-end** against the live API.

**Dependency chain summary:**
Cloudflare DNS → FortiGate TLS certs → Google OAuth redirect URI → NUC deployment works with OAuth → Full end-to-end test

---

## PHASE A — Third-party account setup (do these first, they unlock everything else)

### A1 — Cloudflare: hand over `andrewlass.com` and create DNS records

Cloudflare will become the authoritative DNS for `andrewlass.com`. GoDaddy stays as the registrar (where you pay for the domain) but stops serving DNS.

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
   # Should show Cloudflare's nameservers
   ```

### A2 — Cloudflare: create app DNS records and DDNS token

1. In Cloudflare → **DNS** for `andrewlass.com`, add three A records:
   ```
   Type: A   Name: diary.perfectday                Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   Type: A   Name: api.diary.perfectday             Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   Type: A   Name: media.diary.perfectday           Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: ON
   ```
   To find your NUC WAN IP: `curl https://api.ipify.org` (run from the NUC or any home device).
   Proxy must be **ON** (orange cloud) — Cloudflare handles the public-facing TLS; FortiGate uses a
   Cloudflare Origin Certificate for the CF↔origin hop. See [`deploy/cloudflare.md`](deploy/cloudflare.md)
   § Cloudflare Origin Certificate setup.

2. Create a scoped API token for DDNS updates:
   - Cloudflare → **My Profile** → **API Tokens** → **Create Token**
   - Use template **Edit zone DNS**
   - Under **Zone Resources** → select `andrewlass.com` only
   - Click **Continue to summary** → **Create Token**
   - **Save this token** — you will not see it again. Store in your password manager.

3. Verify DNS resolves:
   ```bash
   dig +short diary.perfectday.andrewlass.com
   # Should return your NUC WAN IP
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
     (You will add the production URI in step B3 after FortiGate TLS is set up)
   - Click **Create**
   - **Download the JSON** and store `client_id` and `client_secret` in your password manager.

6. Add yourself as a test user (required while app is "External" and unverified):
   - **OAuth consent screen** → **Test users** → **Add users** → add your Gmail address

### A4 — SendGrid: create account and get API key

SendGrid is needed because residential IPs are blocked by most mail servers. It handles email delivery for magic links, password resets, etc. Phase 1 only needs it for the NUC deployment (not local dev).

1. Create a free account at [sendgrid.com](https://sendgrid.com). Free tier allows 100 emails/day, which is plenty for PoC.
2. Verify your email address when prompted.
3. Complete the required sender identity setup:
   - Go to **Settings** → **Sender Authentication**
   - Choose **Single Sender Verification** (easiest for PoC)
   - Fill in: From Name = `Perfect Day`, From Email = `pd@bdsys.net`, Reply To = your email
   - Click **Create** → check your email → click the verification link
4. Get an API key:
   - **Settings** → **API Keys** → **Create API Key**
   - Key name: `perfect-day-poc`
   - Permission: **Restricted Access** → enable **Mail Send** → Full Access
   - Click **Create & View**
   - **Copy the key immediately** — it is only shown once. Save to password manager.
5. (Optional but recommended) Set up domain authentication for better deliverability:
   - **Settings** → **Sender Authentication** → **Authenticate Your Domain**
   - Domain: `andrewlass.com`, Link branding: off
   - SendGrid will give you DNS records to add in GoDaddy (CNAME records for `s1._domainkey.andrewlass.com` etc.)
   - Add them in GoDaddy DNS, then click **Verify** in SendGrid

### A5 — Anthropic: get API key

1. Go to [console.anthropic.com](https://console.anthropic.com).
2. **API Keys** → **Create Key** → name it `perfect-day-poc`.
3. Copy and store in password manager.
4. Add billing/credits if prompted (pay-as-you-go; PoC usage will be minimal).

---

## PHASE B — NUC deployment

### B1 — Bootstrap the NUC server (run once)

```bash
ssh andrew@<NUC_IP>
git clone git@github.com:bdsys/perfect-day.git ~/perfect-day
cd ~/perfect-day
sudo ./scripts/nuc/00-bootstrap.sh
```

This installs Docker, UFW firewall, fail2ban, creates the `perfectday` service user and `/opt/perfect-day/`. Also adds `andrew` to the `docker` group (re-login required).

### B1.5 — Set up GitHub deploy key for git clone

```bash
sudo ssh-keygen -t ed25519 -C "perfect-day-nuc-deploy" -f /root/.ssh/id_ed25519 -N ""
sudo cat /root/.ssh/id_ed25519.pub
```

Add the printed public key to GitHub: repo → **Settings** → **Deploy keys** → **Add deploy key** (read-only).

Verify: `sudo ssh -T git@github.com` → should print "Hi bdsys/perfect-day! You've successfully authenticated..."

### B2 — Provision secrets on NUC

```bash
cd ~/perfect-day
sudo ./scripts/nuc/10-secrets.sh
```

The script will prompt for:
- `ANTHROPIC_API_KEY` — from A5
- `GOOGLE_CLIENT_ID` — from A3
- `GOOGLE_CLIENT_SECRET` — from A3
- `SENDGRID_API_KEY` — from A4

It auto-generates all crypto keys. Output: `/etc/perfect-day/app.env` (mode 600).

**⚠️ Back up `/etc/perfect-day/app.env` to your password manager immediately after this step.** If the disk dies without a backup, all encrypted OAuth tokens become permanently unreadable.

### B3 — First deploy

```bash
cd ~/perfect-day
sudo ./scripts/nuc/20-deploy.sh
```

Clones the repo, runs migrations, starts all 8 services. If `/readyz` returns 503 afterward:
```bash
cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh
```

If you have re-run `10-secrets.sh` (which regenerates `POSTGRES_PASSWORD`), you must wipe the Postgres volume first — otherwise the new password won't match what the DB was initialized with:
```bash
sudo ./scripts/nuc/20-deploy.sh --clean
```

`--clean` wipes all `perfect-day_*` Docker volumes across both `--profile nuc` and `--profile dev` (so pgadmin containers from stray dev invocations don't block the volume wipe). The wipe runs after `git pull` so compose sees the current YAML before stopping services.

### B3.5 — Full reinstall (when incremental cleanup is not working)

When iterative fixes fail, do a full nuke and rebuild from scratch. This is the preferred approach for any "state got messy" situation — it's deterministic and takes about 5 minutes.

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
# 2. Re-provision secrets (will re-prompt for all four API keys):
sudo git clone git@github.com:bdsys/perfect-day.git /opt/perfect-day
cd /opt/perfect-day
sudo ./scripts/nuc/10-secrets.sh

# 3. Deploy (no --clean needed; volumes are already gone):
sudo ./scripts/nuc/20-deploy.sh
```

**Why this fixes the recurring Postgres auth failure:** There are six independent state sources on the NUC (running containers across two Docker profiles, named volumes, `/etc/perfect-day/app.env`, `/opt/perfect-day` repo, systemd unit). The auth failure happens when `10-secrets.sh` regenerates `POSTGRES_PASSWORD` but the `postgres_data` volume survives — Postgres only reads the password on first init, so the env and on-disk hash drift. `99-teardown.sh` clears all six sources simultaneously so the next deploy starts from a known-clean state.

### B4 — Cloudflare DDNS setup

Your home IP changes occasionally. The `cloudflare-ddns` sidecar container (already in `docker-compose.yml`) keeps the DNS A records current.

**What you need from Cloudflare (do this first):**
1. An API token with `Zone.DNS:Edit` scope on your zone — see `deploy/cloudflare.md` § 2.1 for exact steps.
2. Your Zone ID — found on the Cloudflare dashboard → your zone → right sidebar.

**Provision the config file on the NUC:**

If you haven't run `10-secrets.sh` yet, you'll be prompted for the token and zone ID automatically. If you already ran it and skipped those prompts, either re-run the script or write the file manually (see `deploy/cloudflare.md` § 2.2 for the one-shot `sudo tee` command).

**Start the updater:**

The DDNS sidecar starts automatically on the next `sudo ./scripts/nuc/20-deploy.sh`. If the NUC is already deployed:

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

**This step requires A2 (DNS records resolving and Cloudflare proxy ON) to be complete first.**

Follow the procedure in [`deploy/nuc.md` → FortiGate Virtual Server setup](deploy/nuc.md#fortigate-virtual-server-setup). It covers:

1. Generate a CSR on FortiGate and obtain a Cloudflare Origin Certificate (multi-SAN, 15-year validity)
2. Create a single HTTPS Virtual Server on WAN:443 with one realserver → NUC:80 (Caddy edge), and bind an HTTP health-check monitor at the VIP level
3. One firewall policy permitting inbound HTTPS from Cloudflare IP ranges only

When complete, verify from off-network:

```bash
curl -I https://diary.perfectday.andrewlass.com/healthz    # Expect: 200 from Next.js
curl -I https://api.diary.perfectday.andrewlass.com/healthz # Expect: 200 from FastAPI
dig +short diary.perfectday.andrewlass.com                  # Expect: Cloudflare anycast IPs
```

### B5.5 — Bring up the Caddy edge on the NUC

After deploying (B3) and configuring FortiGate (B5), start the Caddy edge container if it is not already running:

```bash
cd /opt/perfect-day
docker compose --profile nuc up -d edge
```

Verify Host-header routing is working from inside the NUC LAN:

```bash
curl -sH "Host: diary.perfectday.andrewlass.com" http://<NUC_LAN_IP>:80/healthz
# Expect: 200 response from Next.js

curl -sH "Host: api.diary.perfectday.andrewlass.com" http://<NUC_LAN_IP>:80/healthz
# Expect: {"status":"ok"} from FastAPI
```

See [`deploy/caddy/README.md`](deploy/caddy/README.md) for full documentation and local debug workflow.

### B6 — Add production Google OAuth redirect URI

Now that HTTPS is working, add the production callback URL:

1. Google Cloud Console → **APIs & Services** → **Credentials** → your OAuth 2.0 client
2. Under **Authorized redirect URIs**, add:
   ```
   https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback
   ```
3. Click **Save**

### B7 — Backups

```bash
cd ~/perfect-day
sudo ./scripts/nuc/30-backup.sh
```

Configure rclone for Backblaze B2 when prompted. Sets up daily encrypted `pg_dump` backups. (~$5/mo storage cost)

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

## PHASE C — Web UI audit and fixes

**Do this locally before NUC deployment** (much easier to debug). Walk every route against the local stack:

| Route | What to check |
|---|---|
| `/register` | Form submits, redirects to `/diaries` on success |
| `/login` | Email+password and Google OAuth button both work |
| `/diaries` | Lists owned diaries, "Create diary" flow works |
| `/diaries/[id]` | Shows entry timeline, "Scan now" button, "Connect Google Calendar" link |
| `/entries/[id]` | Shows draft, edit body inline, Publish button works |

Fix any broken wiring before moving to NUC deployment. Known gaps from `POC_PHASE1_LOCAL_TESTING.md`:
- Restore UI for soft-deleted diaries and entries (not yet built)
- Entry hard-delete grace period UI
- Entry restore UI

---

## PHASE D — README and Codecov (after NUC is stable)

### D1 — Sign up for Codecov

1. Go to [codecov.io](https://codecov.io), sign in with GitHub.
2. Add `bdsys/perfect-day` repository.
3. Copy the upload token from the Codecov dashboard.
4. `gh secret set CODECOV_TOKEN` (paste the token when prompted).

### D2 — Update `ci.yml` integration test job

Change the pytest run to generate `coverage.xml`:
```yaml
run: pytest tests/unit tests/integration -q --timeout=120 --cov=app --cov-report=xml
```

Add a coverage upload step after the pytest step:
```yaml
- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v4
  with:
    token: ${{ secrets.CODECOV_TOKEN }}
    files: apps/api/coverage.xml
    flags: integration
    fail_ci_if_error: false
```

### D3 — Create `README.md` at repo root

See spec at `docs/superpowers/specs/2026-05-23-readme-design.md`. Key sections:
- Badge row: 6 CI job badges (Shields.io) + 1 Codecov coverage badge
- One-line project description
- Quick start (4 commands: `bootstrap`, `api`, `web`, `test`)
- "Where to go next" table (4 rows)
- Full doc index: design/, deploy/, operations & reference

### D4 — Verify badges and doc links

1. Push branch → all 6 CI badges show green in README on GitHub
2. Coverage badge shows a percentage (not "unknown") after first CI run
3. Clicking each badge navigates to the Actions workflow page
4. All doc links in the index resolve (no 404s)

---

## PHASE E — CD wiring (optional, after NUC is stable)

```bash
ssh-keygen -t ed25519 -C "perfect-day-deploy" -f ~/.ssh/pd_deploy -N ""
ssh-copy-id -i ~/.ssh/pd_deploy.pub perfectday@<NUC_IP>

gh secret set GHCR_TOKEN          # GitHub PAT with write:packages
gh secret set NUC_SSH_PRIVATE_KEY < ~/.ssh/pd_deploy
gh secret set NUC_HOST --body "<NUC_IP>"
gh secret set NUC_USER --body "perfectday"
gh variable set DEPLOY_ENABLED --body "true"
```

After this, push to `main` → build → deploy → smoke test is fully automated.

---

## Dependency map

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

B1 (NUC bootstrap) → B1.5 (deploy key) → B2 (secrets) → B3 (first deploy) → B4+B5 → B6 → B7 (backups) → B8 (smoke test)

C (Web UI audit) — do locally, ideally before B3 to avoid debugging on NUC

D (README + Codecov) — after B8, NUC stable
E (CD) — after B8, NUC stable
```

---

## Known Gaps / Flagged Issues

- **`require_reauth` in `app/core/auth.py:59`** — calls `loop.run_until_complete()` inside a running async loop. Not triggered in Phase 1 (no admin endpoints use it yet), but will break in Phase 2. Flag for fixing before Phase 2.
- **Google Photos integration is deferred** — Calendar only for Phase 1. Photos in Phase 2.
- **LLM draft generation** requires `ANTHROPIC_API_KEY` and won't run in test mode. Use `make test-live` to exercise it manually once keys are set.
- **Web UI soft-delete restore flows** — entry/diary restore UI not yet built (Phase 1 gap, non-blocking).
