# POC Phase 1 — Next Steps

Where things stand as of 2026-05-22 and what to do next.

---

## Current State

- PR #3 (`poc-p1`) is **CI-green** (all 6 jobs pass). Merge it to `main` before starting anything else.
- Backend API is **complete** for Phase 1: auth, diary/entry CRUD, Google OAuth, scan worker, hard-delete flows, rate limiting.
- Web UI has **real pages** (login, register, diary list, diary timeline, entry detail) but has not been tested end-to-end against the live API.
- All deployment and local-dev scripts exist and are documented.

---

## Step 0 — Add README + Codecov

Before merging PR #3, add a professional repo root README and wire up live coverage badges.
Spec: `docs/superpowers/specs/2026-05-23-readme-design.md`

### 0a — Sign up for Codecov

1. Go to [codecov.io](https://codecov.io), sign in with GitHub
2. Add `bdsys/perfect-day` repository
3. Copy the upload token from the Codecov dashboard
4. `gh secret set CODECOV_TOKEN` (paste the token when prompted)

### 0b — Update `ci.yml` integration test job

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

### 0c — Create `README.md` at repo root

See spec for full content. Key sections:
- Badge row: 6 CI job badges (Shields.io) + 1 Codecov coverage badge
- One-line project description
- Quick start (4 commands: `bootstrap`, `api`, `web`, `test`)
- "Where to go next" table (4 rows)
- Full doc index: design/, deploy/, operations & reference

### Verify

1. Push branch → all 6 CI badges show green in README on GitHub
2. Coverage badge shows a percentage (not "unknown") after first CI run
3. Clicking each badge navigates to the Actions workflow page
4. All doc links in the index resolve (no 404s)

---

## Step 1 — Merge PR #3

```bash
gh pr merge 3 --repo bdsys/perfect-day --squash
```

---

## Step 2 — Local Environment Setup

Full instructions: `POC_PHASE1_LOCAL_TESTING.md`. Short version:

```bash
make bootstrap        # generates secrets, starts Docker infra, runs migrations, installs deps
```

Then open `apps/api/.env` and fill in the three keys that bootstrap leaves blank:

```
GOOGLE_CLIENT_ID=...        # Google Cloud Console → APIs & Services → Credentials
GOOGLE_CLIENT_SECRET=...    # same credential
ANTHROPIC_API_KEY=...       # console.anthropic.com
```

These can be left blank to test auth + diary/entry CRUD without integrations.

Start the stack:

```bash
make api    # FastAPI on :8000, hot-reload  (separate terminal)
make web    # Next.js on :3000, hot-reload  (separate terminal)
```

Validate:

```bash
make test                                    # unit + integration suite (~3-5 min)
./scripts/smoke-test.sh http://localhost:8000  # curl walkthrough of every endpoint
```

Then open `http://localhost:3000`, register, create a diary, and walk the golden path manually.

---

## Step 3 — Web UI Audit

The web pages exist but haven't been tested against a live API. Walk through each route and note what's broken or missing:

| Route | What to check |
|---|---|
| `/register` | Form submits, redirects to `/diaries` on success |
| `/login` | Email+password and Google OAuth button both work |
| `/diaries` | Lists owned diaries, "Create diary" flow works |
| `/diaries/[id]` | Shows entry timeline, "Scan now" button, "Connect Google Calendar" link |
| `/entries/[id]` | Shows draft, edit body inline, Publish button works |

Fix any broken wiring before moving to NUC deployment — it's much easier to debug locally.

---

## Step 4 — NUC Deployment

Full instructions: `POC_PHASE1_DEPLOYMENT.md`. Five sequential steps:

### 4a — Bootstrap the NUC (run once)
```bash
scp scripts/nuc/00-bootstrap.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/00-bootstrap.sh
```

Installs Docker, UFW, fail2ban, creates `perfectday` service user and `/opt/perfect-day/`.

### 4b — Provision secrets
```bash
scp scripts/nuc/10-secrets.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/10-secrets.sh
```

Prompts for `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SENDGRID_API_KEY`.
Auto-generates all crypto keys. Output: `/etc/perfect-day/app.env` (chmod 600).

**Back up `/etc/perfect-day/app.env` to your password manager immediately.** If the disk dies without a backup, all encrypted OAuth tokens are permanently unreadable.

### 4c — First deploy
```bash
./scripts/nuc/20-deploy.sh root@<NUC_IP>
```

Clones repo, runs migrations, starts all 7 services. If `/readyz` returns 503 after, run:
```bash
ssh perfectday@<NUC_IP> "cd /opt/perfect-day && ./scripts/seed-minio-bucket.sh"
```

### 4d — Cloudflare DNS + DDNS setup

Full instructions: `deploy/cloudflare.md`. Short version:

1. Add `bdsys.net` to Cloudflare (free account). Import existing GoDaddy records when prompted.
   Change GoDaddy nameservers to Cloudflare's assigned NS servers.
2. In Cloudflare DNS, create three A records pointing to the NUC WAN IP:
   - `diary.perfectday.bdsys.net` → `<NUC WAN IP>`, TTL 300, proxy off
   - `api.diary.perfectday.bdsys.net` → `<NUC WAN IP>`, TTL 300, proxy off
   - `media.diary.perfectday.bdsys.net` → `<NUC WAN IP>`, TTL 300, proxy off
3. Create a scoped Cloudflare API token: `Zone.DNS:Edit` on `bdsys.net` only.
   Store at `/etc/perfect-day/cloudflare-ddns.token` (mode 0600).
4. Check FortiOS 7.4 **Network → DNS → Dynamic DNS** for built-in Cloudflare support.
   If present, configure DDNS there (no container needed). Otherwise, add
   `cloudflare-ddns` container to `docker-compose.yml` — see `deploy/cloudflare.md` § 2.2.
5. Verify: `curl https://api.ipify.org` matches `dig +short diary.perfectday.bdsys.net`.

### 4e — FortiGate edge config (manual, in FortiGate UI)

Create two virtual hosts:

| Vhost | Backend | Port |
|---|---|---|
| `diary.perfectday.bdsys.net` | NUC IP | 3000 |
| `api.diary.perfectday.bdsys.net` | NUC IP | 8000 |

- TLS: Let's Encrypt via FortiGate's ACME client (both vhosts need valid certs — Google OAuth requires HTTPS)
- In Google Cloud Console → OAuth 2.0 Client, add authorized redirect URI: `https://api.diary.perfectday.bdsys.net/v1/integrations/google/callback`

**Note:** Step 4d (Cloudflare DNS) must be complete before FortiGate can get a Let's Encrypt cert,
because ACME HTTP-01 challenge requires the domain to resolve to the NUC's public IP.

### 4f — Backups
```bash
scp scripts/nuc/30-backup.sh root@<NUC_IP>:/tmp/
ssh root@<NUC_IP> bash /tmp/30-backup.sh
```

Sets up daily encrypted `pg_dump` to Backblaze B2. Configure rclone for B2 when prompted.

### Validate deployment
```bash
./scripts/smoke-test.sh https://api.diary.perfectday.bdsys.net
# Expect: 16 PASS lines
```

---

## Step 5 — Enable CD (optional, after deployment is stable)

Once the NUC is live and stable, wire up GitHub Actions for auto-deploy on push to `main`:

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

## Known Gaps / Flagged Issues

- **Web UI is untested end-to-end** — Step 3 above. Expect some wiring issues.
- **Google Photos integration is deferred** — per PoC scope, Calendar only for Phase 1. Photos comes in Phase 2.
- **`require_reauth` in `app/core/auth.py:59`** — calls `loop.run_until_complete()` from inside an already-running async loop. Not exercised in Phase 1 (no admin endpoints use it yet), but it will break when it is. Flag for fixing before Phase 2.
- **LLM draft generation** requires `ANTHROPIC_API_KEY` and won't run in test mode. Use `make test-live` to exercise it manually once keys are set.
