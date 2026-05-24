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
   Type: A   Name: diary.perfectday                Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: OFF
   Type: A   Name: api.diary.perfectday             Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: OFF
   Type: A   Name: media.diary.perfectday           Value: <YOUR_NUC_WAN_IP>   TTL: 300   Proxy: OFF
   ```
   To find your NUC WAN IP: `curl https://api.ipify.org` (run from the NUC or any home device).
   Proxy must be **OFF** (grey cloud, not orange) — FortiGate handles TLS; Cloudflare proxying breaks this.

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
   - Fill in: From Name = `Perfect Day`, From Email = `noreply@bdsys.net`, Reply To = your email
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
scp scripts/nuc/00-bootstrap.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/00-bootstrap.sh
```

This installs Docker, UFW firewall, fail2ban, creates the `perfectday` service user and `/opt/perfect-day/`.

### B2 — Provision secrets on NUC

```bash
scp scripts/nuc/10-secrets.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/10-secrets.sh
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
./scripts/nuc/20-deploy.sh root@<NUC_IP>
```

Clones the repo, runs migrations, starts all 7 services. If `/readyz` returns 503 afterward:
```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh"
```

### B4 — Cloudflare DDNS setup

Your home IP changes occasionally. The DDNS updater keeps the DNS records current.

First, check if your FortiGate has built-in Cloudflare DDNS support:
- Log in to FortiGate UI → **Network** → **DNS** → **Dynamic DNS**
- If Cloudflare is listed as a provider, configure it there using the token from A2.

If FortiGate doesn't support Cloudflare DDNS natively, add the updater to the NUC:
- SSH to NUC → edit `/opt/perfect-day/docker-compose.yml`
- Add the `cloudflare-ddns` service as documented in `deploy/cloudflare.md` § 2.2
- Use the scoped API token from A2

Verify DDNS is working:
```bash
# From any external connection (phone hotspot, etc.):
curl https://api.ipify.org          # your current WAN IP
dig +short diary.perfectday.andrewlass.com   # should match
```

### B5 — FortiGate: virtual hosts and TLS certificates

**This step requires A2 (DNS records resolving) to be complete first** — Let's Encrypt needs to reach your NUC via HTTP to verify ownership.

In the FortiGate UI:

1. **Create two virtual hosts / VIPs:**

   | Virtual Host | Backend IP | Backend Port |
   |---|---|---|
   | `diary.perfectday.andrewlass.com` | NUC IP | 3000 |
   | `api.diary.perfectday.andrewlass.com` | NUC IP | 8000 |

   For each one: Policy & Objects → Virtual IPs → New
   - External interface: WAN interface
   - External IP: WAN IP (or "any")
   - Mapped IP: NUC internal IP
   - Port forwarding: 443 → 3000 (or 8000)

2. **Enable Let's Encrypt certificates for both domains:**
   - System → Certificates → Local → Create/Import → Let's Encrypt
   - Add `diary.perfectday.andrewlass.com` and `api.diary.perfectday.andrewlass.com`
   - FortiGate handles ACME HTTP-01 challenge automatically

3. **Create firewall policies** to allow HTTPS traffic through to each VIP.

4. **Add HTTP→HTTPS redirect** policy for port 80.

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
scp scripts/nuc/30-backup.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/30-backup.sh
```

Configure rclone for Backblaze B2 when prompted. Sets up daily encrypted `pg_dump` backups. (~$5/mo storage cost)

### B8 — Validate production deployment

```bash
./scripts/smoke-test.sh https://api.diary.perfectday.andrewlass.com
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
            └─ B5 (FortiGate TLS certs)  ← requires DNS resolving to NUC
                 └─ B6 (add prod Google OAuth redirect URI)

A3 (Google Cloud project + OAuth creds)
  └─ B2 (secrets on NUC — needs GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET)
       └─ B6 (add prod redirect URI)

A4 (SendGrid API key)
  └─ B2 (secrets on NUC — needs SENDGRID_API_KEY)

A5 (Anthropic API key)
  └─ B2 (secrets on NUC — needs ANTHROPIC_API_KEY)

B1 (NUC bootstrap) → B2 (secrets) → B3 (first deploy) → B4+B5 → B6 → B7 (backups) → B8 (smoke test)

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
