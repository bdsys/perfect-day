# Secrets

All secrets the application handles, where each is stored, who needs it at runtime, and how rotation works.

## Secret inventory

| Secret | Purpose | Consumer | Storage (NUC) | Storage (cloud) | Storage (CX21 hybrid) | Rotation cadence |
|---|---|---|---|---|---|---|
| `master_secret` | HKDF source for photo + OAuth-token KEKs | API process | sops YAML | KMS secret | **Not present in default mode.** Present only during operator-triggered promotion — see § Hybrid escalation | On suspected compromise only; re-wrap all DEKs |
| `oauth_token_secret` | AES-GCM key for OAuth token encryption (separate from master) | API process | sops YAML | KMS secret | Not present (OAuth token decryption runs on NUC Celery worker) | On suspected compromise only |
| `backup_age_public_key` | Public key used to encrypt `pg_dump` archives | Backup Celery task | sops YAML | KMS secret | Not present | On operator change |
| `backup_age_private_key` | Private key to decrypt backup archives | Operator only — never on the app host | Separate device / vault | Separate KMS key | Not present | On operator change |
| `jwt_signing_key` | HS256 (or RS256 private key) for JWT signing and verification | API process | sops YAML | KMS secret | **Not present (CX21 holds verification key only — see below)** | See rotation procedure below |
| `jwt_signing_key_previous` | Previous JWT key accepted during rotation window | API process | sops YAML | KMS secret | Not present | Drop after 30-day window |
| `jwt_verification_key` | HS256 shared secret or RS256 public key — verifies JWTs on CX21 | CX21 FastAPI | N/A | N/A | sops YAML on CX21 | Rotated with `jwt_signing_key` |
| `google_oauth_client_secret` | Google OAuth 2.0 client secret | API process | sops YAML | KMS secret | Not present | As needed (manual) |
| `facebook_oauth_client_secret` | Facebook app secret | API process | sops YAML | KMS secret | Not present | Every 90 days |
| `apple_signin_private_key` | Apple Sign In private key (asymmetric; `AuthKey_*.p8`) | API process | sops YAML | KMS secret | Not present | N/A (asymmetric; revoke+reissue via Apple developer portal) |
| `anthropic_api_key` | Anthropic API access | API process | sops YAML | KMS secret | Not present | On staff change or suspected leak |
| `gemini_api_key` | Gemini API access (LLM fallback) | API process | sops YAML | KMS secret | Not present | On staff change or suspected leak |
| `sendgrid_api_key` | Email delivery | API process | sops YAML | KMS secret | Not present | On staff change or suspected leak |
| `expo_push_access_token` | Expo Push Notification service | API process | sops YAML | KMS secret | Not present | On staff change or suspected leak |
| `postgres_password` | Database access | API process, Celery worker, Alembic | sops YAML | KMS secret | Replica password only (separate credential, read-only access) | On staff change or suspected leak |
| `redis_password` | Redis access | API process, Celery worker | sops YAML | KMS secret | sops YAML on CX21 (CX21 API proxies writes to NUC Redis over WG) | On staff change or suspected leak |
| `minio_access_key` / `minio_secret_key` | Object store access | API process, Celery worker | sops YAML | KMS / provider IAM | **R2 access key + secret (not MinIO)** — sops YAML on CX21 | On staff change or suspected leak |
| `r2_access_key` / `r2_secret_key` | Cloudflare R2 object store (hybrid photo path) | CX21 FastAPI + NUC Celery worker | Not present in NUC-only | N/A | sops YAML on CX21; also injected to NUC Celery for photo ingest writes | On staff change or suspected leak |
| `wg_private_key_cx21` | WireGuard private key for CX21 peer | CX21 WireGuard | N/A | N/A | `/etc/wireguard/wg0.conf` (system secret, not app secret) | On suspected compromise |
| `mtls_client_cert_cx21` | mTLS client cert for DEK-unwrap RPC | CX21 FastAPI | N/A | N/A | sops YAML on CX21 (cert + private key) | Annual renewal |
| `mtls_server_cert_nuc` | mTLS server cert for DEK-unwrap RPC endpoint | NUC FastAPI | sops YAML | N/A | N/A | Annual renewal |
| Observability tokens (Sentry, Grafana Cloud, Better Stack) | Error reporting, log ingestion, uptime monitoring — see `design/observability.md` | API process, Celery worker, host Promtail | sops YAML | KMS secret | sops YAML on CX21 (Sentry DSN, Grafana agent token) | On staff change or suspected leak |
| `ssh_deploy_key` | GitHub Actions → NUC SSH (deploy pipeline) | GitHub Actions runner | GitHub Actions secret | GitHub Actions secret | GitHub Actions secret (separate key for CX21) | On staff change |
| `sops_age_key` | Decrypts the sops YAML at deploy time on the host | GitHub Actions runner (deploy only) | GitHub Actions secret | N/A (cloud uses KMS) | GitHub Actions secret (separate age key for CX21 sops file) | On operator change |

## Storage backends

### Single-host (NUC)

All application secrets live in a `sops`-encrypted YAML file committed to the repo (e.g. `secrets/production.enc.yaml`). The file is encrypted with an `age` key whose private key lives on a YubiKey. At boot:

1. Operator unlocks the YubiKey.
2. `sops --decrypt secrets/production.enc.yaml` produces a plaintext YAML.
3. The plaintext is injected into the container environment via `docker compose`; the file is never written to disk.

**Documented compromise of this approach:** the sops age key + the plaintext secret file are both present on the NUC host (for the duration the compose stack is running). A host-level attacker can read the process environment. This is acceptable for a personal/family home-lab deployment, not for multi-user production. A cloud deployment must use a managed secret manager.

### Cloud

Managed secret manager (provider TBD — see `deploy/cloud.md`). Each secret is a versioned entry; the app resolves secrets at startup. KMS-backed KEK required for `master_secret` and `oauth_token_secret`. Access logs are part of the audit trail.

### Local development

Each developer maintains a `.env.development` file (gitignored) with non-production values: a placeholder Anthropic key, dummy Google/Facebook/Apple OAuth clients, a local Redis/Postgres password. `.env.production` is never committed. CI injects secrets from GitHub Actions secrets at deploy time.

## Hybrid escalation

When the operator promotes the CX21 replica to primary (see `deploy/hybrid.md` § Escalation runbook), `master_secret` is temporarily present on the CX21. This is a documented privacy degradation accepted in exchange for write availability during a long NUC outage.

**During promotion:** `master_secret` is pasted into the CX21 process environment from a sops-encrypted backup stored in 1Password (`perfectday-master-secret-backup.age`). It lives in process memory and in the Docker Compose env file for the duration of the promotion period. It is NOT stored in any persistent CX21 secret store.

**On recovery (NUC returns):** `master_secret` MUST be rotated before the NUC is reinstated as primary. Procedure:
1. Generate `master_secret_v2` (32 random bytes).
2. Run the re-wrap script per § Rotation / `master_secret` below.
3. Remove `master_secret` from CX21 environment (`grep -i master /proc/$(pgrep -f fastapi)/environ` must return nothing after restart).
4. Record the rotation in the audit log.

Failure to rotate means the same `master_secret` remains on both hosts and the security model is not restored to "master_secret on NUC only." This must not be skipped.

## Rotation procedures

### `master_secret`

1. Generate a new 32-byte random `master_secret_v2`.
2. Run the re-wrap script: for every `photos.dek_ciphertext` row, decrypt the DEK using the old KEK, re-encrypt using the new KEK, write back. The `key_version_byte` at the start of `dek_ciphertext` identifies which master version was used to wrap.
3. Same re-wrap for `oauth_tokens.access_token_ciphertext` and `oauth_tokens.refresh_token_ciphertext`.
4. Swap `master_secret` → `master_secret_v2` in the secret store. Restart the API.
5. Delete old `master_secret` from the secret store after verifying the app is healthy.

**Window:** re-wrap can be done offline (stop the API, re-wrap, restart) or online if the re-wrap script takes the key-version byte into account. For PoC, offline is fine.

### `jwt_signing_key`

1. Generate `jwt_signing_key_v2`.
2. Add `jwt_signing_key_previous = jwt_signing_key` to the secret store.
3. Update `jwt_signing_key = jwt_signing_key_v2`.
4. The API now signs new tokens with `v2` and accepts tokens signed by either key.
5. After 30 days (longest refresh token TTL), remove `jwt_signing_key_previous`.

### OAuth client secrets

Each provider rotates differently:
- **Google:** rotate via Google Cloud Console when needed. Brief window where existing OAuth sessions remain valid (access tokens aren't affected; refresh tokens survive client-secret rotation).
- **Facebook:** rotate every 90 days in Meta developer dashboard. Same brief window.
- **Apple:** Apple private keys can't be rotated in place — revoke in Apple developer portal, download a new key, update the secret. Revocation invalidates all Sign In with Apple sessions; users must re-authenticate.

### API keys (Anthropic, SendGrid, etc.)

1. Create a new key in the provider dashboard.
2. Update the secret store.
3. Restart the service.
4. Revoke the old key.

Zero-downtime: the new key is active before the old is revoked. One-minute window where both are live is acceptable.

### `backup_age_private_key`

Rotated only when the operator changes. Re-encrypting historical backups is out of scope (they're encrypted under the old key and remain readable only by the old operator). The new key covers backups going forward; document the handover date.

## Compromise response

### `master_secret` leaked

Assume all encrypted photos and OAuth tokens are decryptable by the attacker.

1. Immediately revoke all `oauth_tokens` (marks `revoked_at`). This forces users to re-grant Google/Facebook scopes on next scan — they'll see a re-auth prompt.
2. Rotate `master_secret` per the procedure above.
3. Notify affected users: "We detected a security incident. Your photos remain encrypted with a new key. Please re-connect your Google account."
4. Rotate `oauth_token_secret` as well (separate key, but belt-and-suspenders).

### `backup_age_private_key` leaked (master_secret intact)

Backups are encrypted at both the `age` layer and the app layer (photos + OAuth tokens are AES-GCM inside the dump). A `backup_age_private_key`-only leak does not expose photos or OAuth tokens in plaintext — the attacker also needs `master_secret`.

1. Rotate the age key (new public key on the backup task, new private key to operator).
2. No user notification required unless `master_secret` was also exposed.

### `jwt_signing_key` leaked

1. Rotate immediately per the procedure above (no 30-day window — go to v2 immediately, drop v1 immediately).
2. Force-logout all users: delete all `refresh_tokens` rows (or mark `revoked_at = now()`).
3. Users must re-login on next request.

### Provider API key leaked (Anthropic, SendGrid, etc.)

Rotate and revoke per the API keys procedure. Check provider logs for unauthorized usage. No user notification unless user data was exposed via the provider's API.

## Audit trail

- **Cloud:** every secret fetch is logged by the managed secret manager. Access logs are part of the security audit trail.
- **NUC/sops:** weaker audit. The plaintext YAML is decrypted once at boot; no per-access log exists. This gap is acknowledged and acceptable for a personal deployment. Add host-level process monitoring if the threat model requires it.

## Local dev checklist (new developer setup)

1. Copy `.env.development.example` to `.env.development`.
2. Fill in placeholder values for Anthropic, Google OAuth (use the dev OAuth client, not production).
3. Never copy production values into `.env.development`.
4. Do not commit any `.env.*` file.
