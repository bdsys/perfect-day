# Hybrid Deployment (NUC + Hetzner CX21 Cloud Edge)

This document describes the hybrid deployment target: a Hetzner CX21 cloud VPS acts as the public edge while the NUC retains all canonical state. For NUC-only deployment see `deploy/nuc.md`; for full cloud deployment see `deploy/cloud.md`.

---

## Goals and non-goals

**Goals:**
- Keep the diary reachable for reads when the NUC is unreachable (power, ISP, reboot).
- Eliminate residential uplink as the photo-serving bottleneck.
- Stay within ~€6–8/mo total additional cost.

**Non-goals:**
- Write availability without operator action. In default mode, writes require the NUC to be reachable.
- Automatic failover. All promotion steps are explicit, documented runbook operations.
- Zero-downtime Postgres upgrades or logical replication. Physical streaming replication only.
- Backup replacement. The CX21 replica is HA, not DR. The `age`-encrypted `pg_dump` on the NUC remains the DR backup.

---

## Topology

```
                ┌──────────────────────────────────────────────┐
                │            Internet / End Users              │
                └────────────────┬─────────────────────────────┘
                                 │ TLS (Let's Encrypt via Caddy)
                  ┌──────────────▼───────────────────┐
                  │   Hetzner CX21 (cloud edge)      │
                  │   Caddy (TLS + reverse proxy)    │
                  │   FastAPI public ingress          │
                  │   Next.js SSR                    │
                  │   Postgres streaming read-replica │
                  │   In-memory DEK cache (TTL 15m)  │
                  │   R2 client (Cloudflare R2)      │
                  └──┬─────────────────────┬──────────┘
                     │ WireGuard 10.42.0.0/24         │
                     │ (Postgres replication,         │
                     │  Redis writes,                 │
                     │  DEK-unwrap RPC)               │
        ┌────────────▼────────────────────────┐       │
        │   NUC (canonical home-lab)          │       │
        │   Postgres primary (canonical)      │       │
        │   Redis (broker + result backend)   │  ┌────▼──────────┐
        │   Celery worker + beat              │  │ Cloudflare R2 │
        │   FastAPI (WG-only, not public)     │  │ Encrypted     │
        │   master_secret via sops+YubiKey    │  │ photo chunks  │
        │   DEK-unwrap RPC (mTLS over WG)    │  │ (AES-256-GCM) │
        │   pg_dump|age daily backup → B2    │  └───────────────┘
        └─────────────────────────────────────┘
```

DNS A records point to the CX21 public IP. The NUC's public IP is no longer the user-facing address; the FortiGate vhost for `diary.perfectday.bdsys.net` is deactivated (NUC accessible only over WireGuard or LAN).

---

## WireGuard layout

| Host | WG IP | Role |
|---|---|---|
| NUC | `10.42.0.1` | Server (ListenPort 51820) |
| CX21 | `10.42.0.2` | Peer |

**MTU:** `1280` on both interfaces. Residential routers typically enforce MTU 1500; WG overhead is ~60 bytes; setting 1280 prevents fragmentation when the WG link crosses residential NAT. Verify with `ping -M do -s 1252 10.42.0.1` from CX21 (1252 payload + 28 ICMP/IP header = 1280).

**Keepalive:** `PersistentKeepalive = 25` on the CX21 peer entry (NUC is behind NAT; CX21 is not).

**Health check:** `wg show wg0 | grep latest-handshake` — alert if handshake age > 5 minutes (see Observability section).

**Traffic over WG:**
- Postgres primary connection string: `postgresql://nuc-replica-user@10.42.0.1:5432/perfectday`
- Redis writes from CX21 API: `redis://:password@10.42.0.1:6379/0`
- DEK-unwrap RPC: `https://10.42.0.1:8443/internal/unwrap-dek` (mTLS, see § DEK-unwrap RPC)

---

## Postgres replication

### Initial setup

```bash
# On NUC (run once):
createuser --replication nuc_replica
# Add to pg_hba.conf (WG IP range):
# host replication nuc_replica 10.42.0.0/24 scram-sha-256
# In postgresql.conf:
# wal_level = replica
# max_wal_senders = 2
# synchronous_commit = off   # can't block on WG-linked replica
psql -c "SELECT pg_create_physical_replication_slot('cx21_replica');"

# On CX21 (run once):
pg_basebackup -h 10.42.0.1 -U nuc_replica -D /var/lib/postgresql/data \
  --wal-method=stream --slot=cx21_replica -P
# Write recovery.conf / postgresql.auto.conf:
primary_conninfo = 'host=10.42.0.1 user=nuc_replica password=... sslmode=require'
primary_slot_name = cx21_replica
hot_standby = on
```

`synchronous_commit = off` is intentional: the WG link has non-zero RTT (~5–20 ms to a European Hetzner DC) and synchronous replication would add that latency to every write. Async replication means up to one WAL segment (~16 MB) of data could be lost if the NUC crashes with no warning — acceptable for a personal diary.

### Lag monitoring

```sql
-- On CX21 replica:
SELECT now() - pg_last_xact_replay_timestamp() AS replica_lag;
```

Alert threshold: lag > 30s (Better Stack uptime check via a small `/internal/replica-lag` endpoint). Degraded-mode threshold: lag > 60s (see § Degraded-mode contract).

### Replication slot

The named slot `cx21_replica` prevents the NUC from vacuuming WAL files that the replica hasn't consumed. **Risk:** if the CX21 replica is offline for an extended period, WAL files accumulate on the NUC and can fill the disk. Monitor `pg_replication_slots.lag_size` on the NUC; drop and recreate the slot if the replica is offline > 7 days.

---

## R2 photo storage

Cloudflare R2 replaces MinIO for photo storage in hybrid mode. R2 is S3-compatible; the application code uses `boto3` and the change is a config-only endpoint swap.

### boto3 config

```python
boto3.client(
    "s3",
    endpoint_url="https://<account-id>.r2.cloudflarestorage.com",
    aws_access_key_id=r2_access_key,
    aws_secret_access_key=r2_secret_key,
    region_name="auto",
)
```

Object key format is unchanged: `{user_id}/{uuid}.enc`.

### R2 bucket settings

- **Public access:** disabled.
- **CORS:** restricted to `https://api.diary.perfectday.bdsys.net` only.
- **Lifecycle rule:** abort incomplete multipart uploads after 1 day.
- **IAM token scope:** `Object Read & Write` on the diary bucket only. No bucket-level admin permissions.

### Egress cost

R2 charges $0 egress; storage is $0.015/GB/mo with 10 GB free. A personal diary with ~500 photos at ~4 MB average = ~2 GB. Monthly cost: effectively $0 for storage at PoC scale; $0 for bandwidth regardless of scale.

### MinIO (NUC)

In hybrid mode, MinIO is retired from the production photo path. Keep it running for local development (`ENV=development`). The `S3_ENDPOINT_URL` environment variable switches between MinIO (dev) and R2 (hybrid/cloud).

---

## DEK-unwrap RPC

Photos are encrypted at rest with per-photo DEKs (see `design/08-security-privacy.md` § Photo encryption). The `master_secret` used to derive the KEK lives exclusively on the NUC in default mode. The CX21 cannot decrypt photos on its own; it must call the NUC to unwrap the DEK.

### RPC endpoint (on NUC)

```
POST https://10.42.0.1:8443/internal/unwrap-dek
mTLS: client cert issued to CX21; server cert issued to NUC internal CA
Request:  {"photo_id": "<uuid>"}
Response: {"dek": "<32-byte hex>"}
```

The endpoint:
1. Looks up `photos.dek_ciphertext` in the NUC Postgres.
2. Derives the KEK: `HKDF-SHA256(master_secret, salt=user_id, info="photo-kek")`.
3. Decrypts the wrapped DEK with the KEK.
4. Returns the raw DEK. The DEK is transmitted inside the mTLS-encrypted WG tunnel.

**`master_secret` never leaves the NUC process in default mode.**

### DEK cache on CX21

The CX21 FastAPI process caches unwrapped DEKs in a process-local dictionary:

```
key: photo_id
value: (dek_bytes, expires_at = now() + 15min)
```

- On photo download request: check cache → if miss, call unwrap RPC → cache result.
- On user logout: invalidate all cached DEKs for that user's photos.
- On NUC unreachable: photo downloads fail with `503` for sessions whose DEK is not already cached.

**Bounded memory:** 32 bytes per cached DEK × 1000 photos = 32 KB. Not a memory concern.

### mTLS certificates

Managed with a small internal CA (one-time setup):

```bash
# Internal CA (store private key offline / in 1Password):
openssl req -newkey rsa:4096 -keyout internal-ca.key -x509 -days 3650 -out internal-ca.crt

# NUC server cert:
openssl req -newkey rsa:2048 -keyout nuc-internal.key -out nuc-internal.csr
openssl x509 -req -CA internal-ca.crt -CAkey internal-ca.key -in nuc-internal.csr \
  -days 825 -out nuc-internal.crt

# CX21 client cert:
openssl req -newkey rsa:2048 -keyout cx21-client.key -out cx21-client.csr
openssl x509 -req -CA internal-ca.crt -CAkey internal-ca.key -in cx21-client.csr \
  -days 825 -out cx21-client.crt
```

Certs are renewed annually. Internal CA private key stored in 1Password, never on either host.

---

## Degraded-mode contract

The CX21 maintains a boolean `nuc_reachable` flag (in-process, not persisted). It flips to `False` when **any** of:

1. WireGuard handshake age > 5 minutes (`wg show` polled every 60s by a background thread).
2. Postgres replica lag > 60s (`pg_last_xact_replay_timestamp()` polled every 30s).
3. `SELECT 1` on the primary at `10.42.0.1:5432` fails 3 consecutive times (checked every 30s, 3-strike window).

When `nuc_reachable = False`:
- **Read endpoints** (GET timeline, GET entry, GET photos with cached DEK): return `200 OK` using the read-replica.
- **Write endpoints** (POST, PATCH, DELETE): return `503` with body:
  ```json
  {"error": "NUC_UNREACHABLE_READONLY", "retry_after": 60}
  ```
- **Photo downloads** with un-cached DEK: return `503` with `{"error": "PHOTO_UNAVAILABLE_NUC_OFFLINE"}`.
- **Web UI banner:** "Diary is in read-only mode — scans paused. New entries will resume when the home lab is reachable."

`nuc_reachable` flips back to `True` when all three checks pass for 2 consecutive cycles (60s recovery hysteresis to avoid flapping).

JWT verification remains online (stateless — the CX21 has the JWT verification key, not just the signing key).

---

## Escalation runbook (NUC down > 4h)

This is a **one-way door** in default mode. Once promoted, recovery requires `master_secret` rotation and re-wrapping every DEK. Do not promote unless the NUC is confirmed to be down for an extended period and writes are required.

### Promotion

1. Confirm the NUC is not reachable and not about to come back (check ISP status, ping, physical access).
2. On CX21: `pg_ctl promote -D /var/lib/postgresql/data` (or `touch /var/lib/postgresql/data/promote` if using trigger file). Verify with `SELECT pg_is_in_recovery();` → must return `f`.
3. Update the CX21 API config: `DATABASE_URL` now points to `localhost:5432` (promoted local PG, no longer replica).
4. Retrieve `master_secret` from its sops-encrypted backup (stored in 1Password as `perfectday-master-secret-backup.age`):
   ```bash
   age --decrypt -i backup.age master_secret.enc > master_secret.txt
   # Paste value into CX21 process environment via Docker Compose override or env file.
   ```
5. Restart FastAPI on CX21. Verify photo decryption works for a test photo.
6. Remove the `503 NUC_UNREACHABLE_READONLY` flag (set `nuc_reachable = True` override in config or restart without the WG health check).
7. DNS: A records already point to CX21. No change needed.
8. Write scans on CX21 Celery worker (if desired): set `DATABASE_URL` to local PG, set `MASTER_SECRET` from step 4, restart Celery. This re-enables scanning and LLM generation.

**Privacy note:** `master_secret` is now present in CX21 process memory. This degrades the security model from "master_secret never leaves NUC" to "master_secret on one cloud VPS." This is a documented, accepted privacy degradation for write availability during a long outage. See `design/secrets.md` § Hybrid escalation.

### Recovery (NUC returns)

When the NUC is repaired and brought back online:

1. **Do not point PG replica at the promoted CX21.** Treat the NUC as a new blank slate for Postgres.
2. On CX21: `pg_basebackup` → transfer back to NUC (or restore from last `age`-encrypted `pg_dump` from B2 and manually replay any writes made during promotion).
3. Reinstate NUC as primary: reconfigure `primary_conninfo` on NUC → start as replica → verify lag converges → promote NUC → demote CX21 back to replica.
4. **Rotate `master_secret`** per `design/secrets.md` § Rotation (generate new 32-byte secret, re-wrap all `dek_ciphertext` rows). Required because `master_secret` was transiently present on CX21.
5. Remove `master_secret` from CX21 process environment. Verify via `grep -i master /proc/$(pgrep -f fastapi)/environ` — must return nothing.
6. Re-enable WG health check on CX21; confirm `nuc_reachable` flips naturally.
7. Update audit log: record the outage window, promotion timestamp, recovery timestamp, and `master_secret` rotation confirmation.

**Annual drill:** in staging, simulate a 4h NUC outage, run this runbook end-to-end, confirm write availability, then run full recovery including `master_secret` rotation.

---

## Cost

| Component | Provider | Est. cost/mo |
|---|---|---|
| CX21 (2 vCPU, 4 GB RAM, 40 GB SSD) | Hetzner Cloud | ~€5.83 |
| R2 storage (10 GB free, $0.015/GB over) | Cloudflare R2 | $0 at PoC scale |
| R2 egress | Cloudflare R2 | $0 (free always) |
| WireGuard | Included in CX21 bandwidth | $0 |
| Backblaze B2 backup | Backblaze | ~$0.01–0.05 |
| **Total** | | **~€6–8/mo** |

This is within the $5–15/mo target budget.

---

## Observability additions (hybrid-specific)

Add to the Grafana Cloud dashboard (see `design/observability.md`):

- **WG handshake age** (seconds) — sourced from `wg show wg0` scraped by a Node Exporter textfile collector.
- **Postgres replica lag** (seconds) — sourced from `pg_last_xact_replay_timestamp()`.
- **`nuc_reachable` flag** — 0/1 gauge emitted by the CX21 FastAPI `/metrics` endpoint.
- **DEK cache hit rate** — counter of RPC calls vs. cache hits.

Better Stack alerts:
- WG handshake age > 5 min → "NUC WireGuard connection lost."
- Replica lag > 30s → "Replica lagging — investigate NUC connectivity."
- `nuc_reachable = 0` for > 5 min → "Diary in read-only mode."

Tag all Sentry errors with `host: cx21` or `host: nuc` to distinguish CX21-side errors from NUC-side errors.

---

## Verification checklist (doc-level)

Before deploying hybrid, confirm the documentation passes:

1. `grep -rn "TBD\|TODO\|XXX" design/ deploy/` — no new unresolved markers introduced by hybrid docs.
2. `grep -rho "deploy/[a-z-]*\.md" design/ deploy/ | sort -u | while read f; do test -f "$f" || echo MISSING: $f; done` — prints nothing.
3. `design/README.md` lists `deploy/hybrid.md` alongside `deploy/nuc.md` and `deploy/cloud.md`.
4. Re-read `deploy/hybrid.md` cold and confirm three intersection points are answered:
   - "What happens when the NUC is offline?" → § Degraded-mode contract.
   - "Where does `master_secret` live in hybrid mode?" → § DEK-unwrap RPC + `design/secrets.md`.
   - "How do I get write availability during a long outage?" → § Escalation runbook.

Operator-runnable verification (at actual deployment time — not part of the documentation pass):

1. **WireGuard:** `wg show` on both peers, last-handshake < 2 min, bidirectional ping over WG IPs, MTU test passes (see § WireGuard layout).
2. **Postgres replica:** `SELECT pg_is_in_recovery();` on CX21 returns `t`; lag < 5s; `pg_replication_slots` on NUC shows slot active.
3. **Read path:** `curl https://diary.perfectday.bdsys.net/healthz` from external host returns 200; timeline entry counts match NUC-direct.
4. **Photo round-trip:** upload photo → fetch via CX21 → decrypt succeeds, latency < 1s per chunk; `grep -i master /proc/$(pgrep -f fastapi)/environ` on CX21 returns nothing.
5. **DEK cache bounds:** read after TTL expiry, observe fresh unwrap RPC call in NUC logs.
6. **Degraded-mode drill:** stop Postgres on NUC for 60s; CX21 returns `503 NUC_UNREACHABLE_READONLY` for writes, 200 for reads; restore NUC, replica catches up without manual intervention.
7. **R2 sanity:** public-read disabled; CORS limited to API origin; lifecycle rule for incomplete multipart uploads present.
8. **Backup integrity:** `pg_dump | age` still runs from NUC daily; CX21 replica is NOT counted as a backup; quarterly DR drill still performed from the B2 archive.
9. **Promotion drill (annual, staging):** simulate long outage, run escalation runbook, confirm writes, run full recovery + `master_secret` rotation.
10. **Observability:** Sentry errors tagged `host: cx21` / `host: nuc`; Grafana panels for replica lag and WG handshake age; Better Stack alerts for WG loss and replica lag > 30s.
