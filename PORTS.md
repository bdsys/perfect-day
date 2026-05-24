# Ports Reference

## Local Development (`make up` / `make infra`)

| Port | Service | URL | Notes |
|------|---------|-----|-------|
| 3000 | Web (Next.js) | http://localhost:3000 | Main app UI |
| 8000 | API (FastAPI) | http://localhost:8000 | REST API; docs at `/docs` |
| 5432 | PostgreSQL | localhost:5432 | DB: `perfectday`, user: `perfectday` |
| 6379 | Redis | localhost:6379 | Celery broker + result backend |
| 9000 | MinIO S3 API | http://localhost:9000 | S3-compatible object storage |
| 9001 | MinIO Console | http://localhost:9001 | Web UI; user: `minioadmin` / `minioadmin` |
| 5050 | pgAdmin 4 | http://localhost:5050 | Postgres web GUI; user: `admin@perfectday.local` / `admin` |

## Server (NUC, production-like)

The FortiGate handles TLS termination and reverse-proxies to the NUC. Only ports 80 and 443 are exposed to the network.

| Port | Service | Public URL |
|------|---------|------------|
| 443 | HTTPS (TLS termination, FortiGate) | https://diary.perfectday.andrewlass.com |
| 80 | HTTP (redirects to HTTPS, FortiGate) | http://diary.perfectday.andrewlass.com |

Internal container ports on the NUC mirror the local dev setup above but are not exposed externally.
