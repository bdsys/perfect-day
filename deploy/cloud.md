# Cloud / VPS Deployment

This document describes the decision criteria and preparation for deploying Perfect Day on a cloud target. **A specific target has not yet been selected.** Fill in the chosen target's specifics when the decision is made.

---

## Status

**TBD.** Populate when a cloud target is chosen.

> **Hybrid alternative:** if you want cloud infrastructure to improve reliability without fully migrating off the NUC, see [`deploy/hybrid.md`](hybrid.md). The hybrid topology keeps Postgres, Redis, and `master_secret` on the NUC while adding a Hetzner CX21 cloud edge for read-availability, TLS, and photo bandwidth — at ~€6–8/mo total.

## Decision criteria

| Criterion | Why it matters |
|---|---|
| Managed Postgres | PITR (point-in-time recovery) replaces the manual `pg_dump` + `age` backup strategy. Required before any commercial launch. |
| S3-compatible object store | The app uses `boto3`; switching from MinIO to any S3-compatible provider (S3, R2, B2, GCS) is a config change. |
| Total cost < $30/mo for PoC scale | Small personal/family diary. Postgres + Redis + compute on a single VPS is enough. |
| EU data residency option | Required for GDPR compliance if users are in the EU. Verify before EU launch. |
| FastAPI / Docker Compose deploy story | App runs as containers; the target must support either Docker Compose directly or equivalent (ECS task definitions, Fly Machines, etc.). |
| Managed Redis | No operational burden for a PoC. Upstash free tier is an option. |

## Candidate shortlist

| Target | Model | Est. cost/mo | Notes |
|---|---|---|---|
| **Hetzner Cloud CX21** | Single VPS, Docker Compose | ~€6 | Cheapest; same ops model as NUC. Manual Postgres backup via `pg_dump`. |
| **Fly.io** | App platform (Machines) | ~$10–20 | Managed Postgres add-on (PITR). Fly Volumes for MinIO. Easy deploy with `flyctl`. |
| **Railway** | Managed containers | ~$10–20 | Postgres + Redis built-in. Simple CI/CD integration. Less control. |
| **Render** | Managed containers | ~$15–25 | Managed Postgres. Good Docker Compose → Render migration story. |
| **AWS (ECS Fargate + RDS + S3)** | Fully managed | ~$50–100+ | Most powerful; overkill for PoC. Consider post-Series-A. |
| **GCP (Cloud Run + Cloud SQL + GCS)** | Serverless containers | ~$20–50 | Cloud Run cold starts may affect latency. |

Recommendation when ready to choose: **Fly.io or Hetzner Cloud CX21**. Fly offers managed Postgres with PITR for ~$7/mo extra; Hetzner is simpler operationally if manual backups are acceptable.

## Differences from NUC deployment

When filling in this doc, cover each of the following topics:

| Topic | NUC approach | Cloud equivalent |
|---|---|---|
| Edge / TLS | FortiGate 7.2+ (WAF, vhost, TLS termination) | Cloud LB, Cloudflare, Fly proxy, or Caddy on VPS |
| Secrets | sops + YubiKey at boot | Managed secret manager (AWS Secrets Manager, GCP Secret Manager, 1Password Connect, Fly secrets) |
| Database | Self-hosted Postgres + pg_dump + age encryption | Managed Postgres with PITR (no `age` layer needed — PITR covers point-in-time restore) |
| Redis | Self-hosted | Managed (Upstash free tier, Fly Redis, ElastiCache) |
| Object storage | Self-hosted MinIO | S3 / Cloudflare R2 / Backblaze B2 — `boto3` client is config-only change |
| Backup strategy | age-encrypted pg_dump → external bucket | Provider PITR + cross-region object replication |
| Celery concurrency | Capped at 2 (NUC RAM constraint) | Scale to match workload |
| Email deliverability | Residential IP → must use SendGrid relay | Datacenter IP → SendGrid relay still recommended but deliverability is better |
| `master_secret` storage | sops YAML on host | KMS-backed secret. **Production deployments must use KMS-backed KEK.** |
| Observability | Grafana Cloud free tier (no change needed) | No change needed |
| Deploy CI step | SSH into NUC + docker compose up | `flyctl deploy` / `railway up` / `docker service update` / etc. |

## Verification (when target is chosen)

1. Provision the target with a minimal stack (Postgres + Redis + MinIO/S3).
2. Deploy the API and run `GET /readyz` — all dependencies must pass.
3. Run the full integration test suite against the provisioned environment: `make test-integration-env TARGET=cloud`.
4. Perform a backup + restore drill: dump → encrypt → restore to a clean Postgres instance → verify `SELECT count(*) FROM users`.
5. Confirm TLS cert is valid and FortiGate rules are not referenced in any startup log.
