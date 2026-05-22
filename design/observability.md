# Observability

## Stack

Budget constraint: free-tier or < $5/mo. Mix of self-hosted and cloud OK.

| Concern | Tool | Tier | Cost |
|---|---|---|---|
| Errors / exceptions | Sentry | Free | $0 — **verify quota at provisioning** (historically ~5k errors/mo) |
| Logs | Grafana Cloud Loki | Free | $0 — **verify quota at provisioning** (historically 50 GB/mo, 14-day retention) |
| Metrics | Grafana Cloud Prometheus | Free | $0 — **verify quota at provisioning** (historically 10k active series) |
| Dashboards / alerts | Grafana Cloud | Free | $0 — bundled with Loki+Prom |
| Uptime / synthetic monitoring | Better Stack | Free | $0 — **verify quota at provisioning** (historically 10 monitors) |
| Tracing | Deferred | — | $0 — add Grafana Tempo (also free tier) post-launch if needed |
| Push notification receipts | Expo | Bundled | $0 — polled by Celery; receipts logged to Loki |

**Total expected cost: $0/mo.**

If any free tier is exhausted, the first fallback is a Hetzner CX11 VPS (~€4/mo) running self-hosted Loki + Prometheus + Grafana. All tooling is drop-in replaceable — the app emits structured JSON to stdout regardless of which Loki endpoint receives it.

**Quota re-verification:** check provider free-tier terms quarterly. Free tiers change; the values above reflect the historical baseline.

## Logging

### Format

All services (FastAPI, Celery worker, Celery beat) emit **structured JSON to stdout**. Docker captures stdout; Promtail (or Vector) tails container logs and ships to Grafana Cloud Loki.

```json
{
  "ts": "2024-10-03T14:00:00.123Z",
  "level": "info",
  "service": "api",
  "request_id": "01J...",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "msg": "entry.published",
  "entry_id": "...",
  "diary_id": "..."
}
```

`request_id` is a UUIDv4 generated at request ingress and propagated to all downstream Celery tasks via task `headers`. This is the forward-compatibility hook for adding distributed tracing later.

### PII rules

**Never log:**
- `users.email` or any raw email address
- `body_markdown` or any entry content
- File paths or object keys that expose user IDs in context (log `photo_id`, not `{user_id}/{uuid}.enc`)
- OAuth `access_token` or `refresh_token` values
- JWT tokens or cookie values

**Acceptable to log:**
- `user_id` (UUIDv4 — not human-readable without a DB lookup)
- `diary_id`, `entry_id`, `photo_id` (internal UUIDs)
- HTTP status codes, endpoint paths, latency
- Error types, Celery task names, queue depths

Use Sentry's `before_send` hook to scrub any PII that accidentally reaches the Sentry SDK.

### Log levels

| Level | When to use |
|---|---|
| `debug` | Disabled in production. Local dev only. |
| `info` | Normal operations: request handled, task completed, scan finished |
| `warning` | Recoverable issue: LLM rate limit, soft-deleted item accessed, retried task |
| `error` | Unhandled exception, failed task (after retries exhausted), missing required dependency |
| `critical` | Startup failure, secret loading failure, encryption system failure |

## Metrics

FastAPI exposes `/metrics` in Prometheus exposition format. The endpoint is bound to localhost or requires an admin token (not public).

### Standard metrics

| Metric | Type | Labels |
|---|---|---|
| `http_request_duration_seconds` | Histogram | `method`, `route`, `status_code` |
| `http_requests_total` | Counter | `method`, `route`, `status_code` |
| `celery_task_duration_seconds` | Histogram | `task_name`, `status` (success/failure) |
| `celery_tasks_total` | Counter | `task_name`, `status` |
| `celery_queue_depth` | Gauge | `queue_name` |
| `db_pool_connections_active` | Gauge | — |
| `db_pool_connections_idle` | Gauge | — |
| `minio_upload_bytes_total` | Counter | — |
| `minio_download_bytes_total` | Counter | — |

### Custom metrics

| Metric | Type | Why |
|---|---|---|
| `scan_run_total` | Counter | `diary_id`, `status` (success/failure) — track scan reliability per diary |
| `llm_tokens_input_total` | Counter | `model` — cost tracking |
| `llm_tokens_output_total` | Counter | `model` — cost tracking |
| `llm_api_errors_total` | Counter | `error_type` — rate-limit vs. API error differentiation |
| `magic_link_requests_total` | Counter | — abuse signal |
| `hard_delete_runs_total` | Counter | `status` — compliance |
| `backup_runs_total` | Counter | `status` — compliance |

## Alerting

Alerts fire to the operator (email or PagerDuty-like webhook — configure at deployment time).

### Page (wake-me-up)

| Condition | Window | Reason |
|---|---|---|
| API 5xx rate > 1% | 5 min | Service degraded |
| `/readyz` returning non-200 | 2 min | Full outage |
| Celery queue depth > 100 | 5 min | Worker backlog / consumer failure |
| `process_hard_deletes` task failure | Any | GDPR compliance — deletions must run |
| Daily backup task failure | Any | Data loss risk |
| LLM API error rate > 5% | 15 min | Scan + draft generation broken |
| OAuth token refresh failure rate > 10% | 1 hour | Integration revoked / API outage |
| TLS certificate expiry < 14 days | Any | Pre-emptive renewal failure |

### Notify (no page)

| Condition | Reason |
|---|---|
| Scan failure for an individual diary | Would page-storm if many diaries fail simultaneously during a Google outage |
| Account deletion spike (> 5 in 1 hour) | Unusual activity signal |
| Observability quota > 80% of free tier | Impending tier exhaustion |
| Email bounce rate > 5% | Deliverability issue |
| Email spam complaint rate > 0.1% | Deliverability issue |

## Dashboards

Three dashboards in Grafana Cloud:

**Operator (system health)**
- API request rate + error rate + p95 latency
- Celery queue depth + task success rate
- DB pool utilisation
- MinIO upload/download bytes
- Recent alerts

**Product**
- Entries created per day (auto vs. manual)
- Scan runs per day (success/failure)
- LLM cost (tokens × $/token by model)
- Active users (unique `user_id` in API logs, last 7 days)

**Compliance**
- `process_hard_deletes` runs (success/failure, accounts deleted, diaries deleted)
- Backup task runs
- Audit log entry count per day

## Tracing (deferred)

Distributed tracing is deferred for PoC. When added post-launch:

- Use OpenTelemetry SDK on FastAPI (auto-instrumentation via `opentelemetry-instrumentation-fastapi`).
- Propagate `traceparent` through Celery tasks via task headers. **This is why `request_id` is propagated from day one** — it's the migration path. When tracing is added, `request_id` becomes the trace ID.
- Target: Grafana Tempo on the Grafana Cloud free tier.
- Sampling: 10% in production (cost control); 100% in staging.

## Sentry configuration

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    integrations=[FastApiIntegration(), CeleryIntegration()],
    traces_sample_rate=0.1,      # stay under free-tier transaction quota
    profiles_sample_rate=0.0,    # disable profiling (not on free tier)
    before_send=_scrub_pii,      # remove email, tokens before send
    environment=settings.env,
    release=settings.git_sha,    # set by CI at build time
)
```

`_scrub_pii` removes any event `extra` or `breadcrumb` keys matching `email`, `token`, `password`, `body_markdown`.

## Self-hosted fallback

If any Grafana Cloud / Sentry free tier is exhausted:

1. Spin up a Hetzner CX11 (~€4/mo).
2. Run `docker compose` with Loki + Prometheus + Grafana + Alloy (Grafana's log/metric collector, replaces Promtail).
3. Update `LOKI_URL` and `PROMETHEUS_URL` env vars; no code changes required.
4. Keep Sentry on the cloud free tier for error capture (it has the best SDK ecosystem).

The Hetzner option is a config change, not an architecture change.
