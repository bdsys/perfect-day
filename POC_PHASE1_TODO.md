# POC Phase 1 — Master Todo

Where things stand as of 2026-05-25 and what is left to do.

> **Operational runbook moved.** All NUC deployment/update/rebuild/rollback procedures live in [`deploy/nuc-ops.md`](deploy/nuc-ops.md). This file now tracks only Phase 1 status, the dependency map, and known gaps.

---

## Current State

- PR #3 (`poc-p1`) has been **merged to `main`**. ✅
- Backend API is **complete** for Phase 1: auth, diary/entry CRUD, Google OAuth, scan worker, hard-delete flows, rate limiting.
- Local environment is **set up and validated** — unit + integration tests pass, smoke test clean. ✅
- Web UI has real pages (login, register, diary list, diary timeline, entry detail) but **not yet tested end-to-end** against the live API.
- Caddyfile templating fix landed (see commit `963bb40`) — `FORTIGATE_LAN_IP` is now rendered into `deploy/caddy/Caddyfile` from `Caddyfile.tmpl` at every deploy. ✅

**Dependency chain summary:**
Cloudflare DNS → FortiGate TLS certs → Google OAuth redirect URI → NUC deployment works with OAuth → Full end-to-end test

---

## What is left

### Phase A — Third-party account setup
**See [`deploy/nuc-ops.md` § Phase A](deploy/nuc-ops.md#phase-a--third-party-account-setup).**
Cloudflare DNS hand-off, Google OAuth project, SendGrid relay, Anthropic API key.

### Phase B — NUC deployment
**See [`deploy/nuc-ops.md` § Phase B](deploy/nuc-ops.md#phase-b--nuc-deployment).**
Bootstrap → secrets → first deploy → DDNS → FortiGate certs → Google prod redirect URI → backups → smoke test.

### Phase C — Web UI audit and fixes

#### C0 — Set up Google Client ID for local dev

The Google Sign-In button on `/login` and `/register` silently hides itself when `NEXT_PUBLIC_GOOGLE_CLIENT_ID` is not set. Complete this before the route audit below.

**Get the credentials** (full instructions in [`deploy/nuc-ops.md` § A3](deploy/nuc-ops.md#a3--google-cloud-create-a-project-and-oauth-credentials)):

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → select your `perfect-day-poc` project (or create one per A3).
2. **APIs & Services** → **Credentials** → click your existing **OAuth 2.0 Client ID** (named `perfect-day-poc-web`).
3. Copy the **Client ID** (ends in `.apps.googleusercontent.com`) and **Client secret** — save both to your password manager if you haven't already.
4. Make sure `http://localhost:8000/v1/integrations/google/callback` is in **Authorized redirect URIs** (should already be there from A3 — add it now if not).

**Wire up the frontend** (local dev only):

5. Create `apps/web/.env.local` (gitignored — never commit this):
   ```
   NEXT_PUBLIC_API_URL=http://localhost:8000
   NEXT_PUBLIC_GOOGLE_CLIENT_ID=<client_id from step 3>
   ```
6. Restart the Next.js dev server (`make web` or `pnpm dev` inside `apps/web`) — Next.js bakes public env vars in at startup, so a restart is required.
7. Navigate to `http://localhost:3000/login` — the **Continue with Google** button should appear below the "or" divider.

**Wire up the backend** (local dev only):

8. Check `apps/api/.env` (or wherever your local backend env lives). It needs:
   ```
   GOOGLE_CLIENT_ID=<client_id from step 3>
   GOOGLE_CLIENT_SECRET=<client_secret from step 3>
   ```
9. Restart the API if you changed its env.

---

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

### Phase D — README and Codecov (after NUC is stable)

#### D1 — Sign up for Codecov

1. Go to [codecov.io](https://codecov.io), sign in with GitHub.
2. Add `bdsys/perfect-day` repository.
3. Copy the upload token from the Codecov dashboard.
4. `gh secret set CODECOV_TOKEN` (paste the token when prompted).

#### D2 — Update `ci.yml` integration test job

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

#### D3 — Create `README.md` at repo root

See spec at `docs/superpowers/specs/2026-05-23-readme-design.md`. Key sections:
- Badge row: 6 CI job badges (Shields.io) + 1 Codecov coverage badge
- One-line project description
- Quick start (4 commands: `bootstrap`, `api`, `web`, `test`)
- "Where to go next" table (4 rows)
- Full doc index: design/, deploy/, operations & reference

#### D4 — Verify badges and doc links

1. Push branch → all 6 CI badges show green in README on GitHub
2. Coverage badge shows a percentage (not "unknown") after first CI run
3. Clicking each badge navigates to the Actions workflow page
4. All doc links in the index resolve (no 404s)

### Phase E — CD wiring (optional, after NUC is stable)

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

Step-by-step procedures for everything in the A/B columns are in [`deploy/nuc-ops.md`](deploy/nuc-ops.md).

---

## Known Gaps / Flagged Issues

- **`require_reauth` in `app/core/auth.py:59`** — calls `loop.run_until_complete()` inside a running async loop. Not triggered in Phase 1 (no admin endpoints use it yet), but will break in Phase 2. Flag for fixing before Phase 2.
- **Google Photos integration is deferred** — Calendar only for Phase 1. Photos in Phase 2.
- **LLM draft generation** requires `ANTHROPIC_API_KEY` and won't run in test mode. Use `make test-live` to exercise it manually once keys are set.
- **Web UI soft-delete restore flows** — entry/diary restore UI not yet built (Phase 1 gap, non-blocking).
