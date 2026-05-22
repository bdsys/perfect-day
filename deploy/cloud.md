# Cloud / VPS Deployment

This document will describe deploying Perfect Day on a cloud VPS or managed services platform. It is a placeholder — fill in when a specific cloud target is selected.

---

## Status

**TBD.** A cloud target has not yet been selected.

## Candidate targets

- Single VPS (DigitalOcean, Hetzner, Linode): Docker Compose on one machine. Equivalent to NUC but with datacenter bandwidth, static IP, and managed disk snapshots.
- Container platforms (Fly.io, Render, Railway): managed runtime, auto-scaling, simpler TLS.
- AWS / GCP / Azure: ECS/Cloud Run + managed Postgres (RDS/Cloud SQL) + managed Redis (ElastiCache/Memorystore) + S3/GCS instead of MinIO.

## Differences from NUC deployment

When filled in, this doc should cover:

- Edge / TLS (cloud LB or Cloudflare instead of FortiGate)
- Secrets management (AWS Secrets Manager / GCP Secret Manager / 1Password Connect instead of sops+YubiKey)
- Database (managed Postgres with PITR instead of pg_dump + age)
- Redis (managed, e.g. Upstash or ElastiCache)
- Object storage (S3 / R2 / B2 instead of self-hosted MinIO — `boto3` S3 client is already compatible)
- Celery worker autoscaling (no hard concurrency cap needed)
- Email deliverability (datacenter IP + SPF/DKIM/DMARC — same SendGrid relay applies, but deliverability is better from a static datacenter IP)
- HA (at minimum: two API replicas behind LB, Postgres standby, Redis replica)
- Observability (Grafana Cloud, Datadog, Honeycomb, or Sentry instead of structlog-to-file)
