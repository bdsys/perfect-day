# Ports Reference

## Local Development (`make up` / `make infra`)

| Port | Service | URL | Notes |
|------|---------|-----|-------|
| 3000 | Web (Next.js) | http://localhost:3000 | Main app UI |
| 8000 | API (FastAPI) | http://localhost:8000 | REST API; docs at `/docs` |
| 5432 | PostgreSQL | localhost:5432 | DB: `perfectday`, user: `perfectday` |
| 6379 | Redis | localhost:6379 | Celery broker + result backend |
| 3900 | Garage S3 API | http://localhost:3900 | S3-compatible photo store (tracker row 41); no web console |
| 9000 / 9001 | MinIO (old store) | — | Only with `--profile minio`; nothing starts it in dev. Removed in row 41's release 2 |
| 5050 | pgAdmin 4 | http://localhost:5050 | Postgres web GUI; user: `admin@perfectday.local` / `admin` |

## Server (NUC, production-like)

Cloudflare proxy (orange cloud) handles the public-facing TLS. FortiGate terminates the CF↔origin TLS hop using a Cloudflare Origin Certificate and forwards plain HTTP to the NUC on the LAN. Only port 443 is accepted inbound on FortiGate (from Cloudflare IP ranges only). HTTP→HTTPS redirects happen at Cloudflare's edge — port 80 is not opened on FortiGate.

| Port | Service | Public URL |
|------|---------|------------|
| 443 | HTTPS (CF↔origin TLS termination, FortiGate; Cloudflare Origin Certificate) | https://perfectday.andrewlass.com |
| 8443 | **Reserved** — internal TLS proxy (future e2e TLS to NUC backends via Caddy/Nginx if needed) | — |

Internal container ports on the NUC mirror the local dev setup above but are not exposed externally.
