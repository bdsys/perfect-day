# Architecture

## Components and connections

```
                ┌──────────────────────────────────────────────┐
                │            Internet / End Users              │
                └────────────────┬─────────────────────────────┘
                                 │ TLS
                  ┌──────────────┴─────────────────┐
                  │   FortiGate 7.4 (edge)         │
                  │   TLS termination, WAF, vhost  │
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

## Decisions locked

- **Worker layout (A3):** Celery + Redis. Separate worker process, Redis as broker and result backend. Tasks: scan jobs, LLM draft generation, photo ingestion, notification dispatch.
  - Watch item: Redis idle RAM ~70–200MB on the 8GB NUC. If memory pressure appears, fallback path is A2 (Postgres-backed queue via `arq` or APScheduler+Postgres jobstore) — task code stays nearly identical.
- **Web/API topology (B2):** Two subdomains.
  - `diary.perfectday.bdsys.net` → Next.js
  - `api.diary.perfectday.bdsys.net` → FastAPI
  - Two TLS certs at FortiGate. CORS allowlist on the API for the web origin and Expo dev tunnel. Mobile uses the API subdomain directly.
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
- Observability — `structlog` + file logs for PoC; production-grade later.
