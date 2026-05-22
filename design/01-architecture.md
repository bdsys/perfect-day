# Architecture

## Components and connections

```
                ┌──────────────────────────────────────────────┐
                │            Internet / End Users              │
                └────────────────┬─────────────────────────────┘
                                 │ TLS
                  ┌──────────────┴─────────────────┐
                  │   Edge proxy                   │
                  │   (TLS termination, WAF, vhost)│
                  └──┬─────────────────┬───────────┘
                     │                 │
       diary.perfectday.bdsys.net  api.diary.perfectday.bdsys.net
                     │                 │
        ┌────────────▼──┐    ┌─────────▼────────────┐
        │ Next.js (web) │    │ FastAPI (API)        │◄──── Expo (mobile)
        │ SSR for OG    │    │ /auth /diaries /...  │      HTTPS + JWT
        └───────────────┘    └─┬─────────┬─────────┬┘      (expo-secure-store)
                               │         │         │
                  ┌────────────┘         │         │
                  │                      │         │
          ┌───────▼──────┐       ┌───────▼──────┐  │
          │ PostgreSQL   │       │ MinIO (S3)   │  │
          │ relational   │       │ photos, enc. │  │
          └──────────────┘       └──────────────┘  │
                  ▲                                │
                  │                                │
          ┌───────┴────────────┐           ┌───────▼──────┐
          │ Celery worker      │           │ Redis        │
          │ - per-diary scans  │◄──────────┤ Celery broker│
          │ - LLM draft jobs   │           │ + result back│
          └─┬───────────┬──────┘           └──────────────┘
            │           │
            ▼           ▼
      Google APIs    LLM API
      (Calendar,     (Anthropic
       Photos)        Claude)
```

On the home-lab deployment the edge proxy is FortiGate 7.4 — see [`deploy/nuc.md`](../deploy/nuc.md). On a cloud deployment this is the cloud load balancer or Cloudflare.

## Deployment targets

Three deployment targets are supported. The application code does not depend on the choice.

- **Single-host home-lab** — Docker Compose on one machine; FortiGate or similar at the edge. See [`deploy/nuc.md`](../deploy/nuc.md) for NUC-specific resource budget, edge config, and known limitations.
- **Cloud VPS / managed services** — Docker Compose on a VPS, or managed Postgres/Redis + container runtime (ECS, Fly, Render, Railway). See [`deploy/cloud.md`](../deploy/cloud.md) (TBD when a target is selected).
- **Hybrid (NUC + Hetzner CX21)** — NUC retains canonical Postgres, Redis, Celery worker, and `master_secret`; a Hetzner CX21 cloud VPS hosts Caddy, FastAPI public ingress, Next.js SSR, and a Postgres streaming read-replica. Photos are stored in Cloudflare R2. A WireGuard tunnel connects the two hosts. The CX21 serves reads when the NUC is unreachable (degraded read-only mode); writes require an explicit operator runbook to promote the replica. See [`deploy/hybrid.md`](../deploy/hybrid.md) for topology, setup, degraded-mode contract, escalation runbook, and cost (~€6–8/mo).

## Decisions locked

- **Worker layout (A3):** Celery + Redis. Separate worker process, Redis as broker and result backend. Tasks: scan jobs, LLM draft generation, photo ingestion, notification dispatch.
  - Worker layout is portable; switching to a Postgres-backed queue (`arq`) is a future option if Redis becomes operationally undesirable.
- **Hybrid topology:** in hybrid mode, the Celery worker and beat stay on the NUC (they require Postgres write access and the photo DEK-unwrap path). The CX21 hosts only the API public ingress, Next.js, and the Postgres read-replica. `master_secret` stays on the NUC in default mode; it is temporarily on CX21 only during an operator-triggered promotion. See [`deploy/hybrid.md`](../deploy/hybrid.md).
- **Web/API topology (B2):** Two subdomains.
  - `diary.perfectday.bdsys.net` → Next.js
  - `api.diary.perfectday.bdsys.net` → FastAPI
  - TLS termination and CORS allowlist at the edge proxy. Mobile uses the API subdomain directly.
  - **CORS policy:** production CORS allowlist is exact-match origins only (`diary.perfectday.bdsys.net`, `api.diary.perfectday.bdsys.net`). Expo dev tunnel (`*.exp.direct`, `*.expo.dev`) is added to the allowlist **only when `ENV=development`**. This must be enforced in code — a misconfigured dev tunnel in a production CORS allowlist would allow any Expo app to make cross-origin API calls. Never deploy with `ENV=development` to the production host.
- **LLM placement (C1):** FastAPI/Celery worker calls cloud LLM (Anthropic Claude primary, Gemini fallback) directly over HTTPS. No cloud-side processing service for PoC. Future migration path to a Lambda/Cloud Run shim is left open.

## Flow notes

- **Web ↔ API:** SSR fetches use a server-side HTTP client; browser-side fetches use the same API. JWT in `Authorization: Bearer`.
- **Expo ↔ API:** Same JWT scheme. Tokens in `expo-secure-store`.
- **API ↔ Postgres:** all relational data.
- **API ↔ MinIO:** boto3 S3 client. Photo delivery proxied through the API (decrypt-and-stream); signed URLs for uploads only.
- **Worker ↔ Google APIs:** stored per-user OAuth refresh tokens (encrypted at rest — see [08-security-privacy.md](08-security-privacy.md)).
- **Worker ↔ LLM:** outbound HTTPS to Anthropic. Retries and backoff handled inside the Celery task.

## Deferred / out-of-scope for PoC architecture

- Notification delivery path — see [07-notifications.md](07-notifications.md).
- Stripe / subscription enforcement — placeholder field only.
- Observability — structured JSON logs to stdout; Grafana Cloud Loki for log aggregation. See [design/observability.md](observability.md) for the full stack.
