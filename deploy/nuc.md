# Home-Lab Deployment (Intel NUC)

This document captures guidance specific to deploying Perfect Day on the home-lab NUC. The application architecture is host-agnostic — see `design/01-architecture.md`. Everything here is deployment-specific.

---

## Hardware

- Intel NUC 4-core x86 1.85 GHz, 8 GB RAM
- Shared with other household services
- Single machine — no high availability

## Edge

- **FortiGate 7.2+:** TLS termination, WAF, virtual hosting
- Two TLS certs (one per hostname, FortiGate ACME issues single-domain certs): `diary.perfectday.andrewlass.com` and `api.diary.perfectday.andrewlass.com`. A third cert for `media.diary.perfectday.andrewlass.com` is added in Phase 2 when photo upload is built.
- FortiGate WAF rule: `media.*` subdomain accepts PUT only (uploads); all other methods blocked at edge
- CORS allowlist on the API for the web origin; Expo dev tunnel allowed only when `ENV=dev`
- **Hybrid mode:** in the hybrid topology, the NUC's FortiGate vhost for `diary.perfectday.andrewlass.com` is deactivated. The NUC is reachable only over WireGuard (or LAN). DNS A records point to the CX21, which handles all public TLS. See [`deploy/hybrid.md`](hybrid.md).

## Resource budget (idle RAM)

| Service | Idle RAM |
|---|---|
| PostgreSQL | 100–200 MB |
| Redis | 70–150 MB |
| FastAPI (2 workers) | 100–150 MB |
| Celery worker (2 processes) | 150–250 MB |
| Celery beat | 50 MB |
| MinIO | 100–200 MB |
| Next.js SSR (Node) | 150–300 MB |
| **Total** | **~720 MB – 1.3 GB** |

Comfortable on 8 GB. Risk zone: heavy backfill + LLM calls simultaneously → CPU saturation before RAM.

## Celery sizing

Cap Celery worker concurrency at 2 for PoC. If memory pressure appears, switching to a single-process async worker (`arq`) is a drop-in option — task interface is nearly identical and cuts worker RAM ~50%.

## Storage

MinIO for photo storage. Storage path (`/data/minio` or similar) can be bind-mounted to a larger drive without application changes — update the Docker Compose volume mount.

## Secrets

`master_secret` and OAuth client secrets are stored in a `sops`-encrypted secrets file. Encrypted with a YubiKey (GPG/age backend). At process start:

```bash
sops -d secrets.enc.yaml > /run/secrets/app.env
# process reads /run/secrets/app.env; file exists only in tmpfs
```

This is a **documented compromise** — the decrypted value lives in process memory on the same host as the database and MinIO. It is acceptable for a personal/family-only deployment where physical access to the host is controlled. It is **not** acceptable for any multi-user or cloud deployment — use a managed secret manager there.

Backup private key (`age` keypair for `age`-encrypted `pg_dump`) is stored on a **separate device** (USB key, second machine, or password manager export), not on the NUC.

## Backup

Celery beat task daily:

1. `pg_dump | age --recipients-file /path/to/backup.pub > backup-$(date +%F).sql.gz.age`
2. Upload to local MinIO bucket `backups/`
3. `rclone sync` to Backblaze B2 (cost ~$5/mo for typical diary data volume)

The `age` private key for decryption lives off-NUC. Without it the backup is unreadable even if an attacker gets the bucket.

**Quarterly DR drill:** restore the most recent backup to a clean Docker Compose stack on a separate machine. Verify diary timeline, photos, and auth all work before declaring the backup valid.

## Email deliverability

The NUC is on a residential IP. Major mailbox providers (Gmail, Outlook) reject or junk transactional email from residential IPs.

**All outbound email must go through SendGrid (or Postmark/SES) as an SMTP relay.** Configure FastAPI to use SendGrid's SMTP endpoint with an API key — never send directly from the NUC's IP.

SPF, DKIM, and DMARC records for `andrewlass.com` are required before any email lands reliably. See [`design/dns-and-email.md`](../design/dns-and-email.md) for the full DNS plan.

## Photo serving caveat

All photo reads are proxied through the API (decrypt-and-stream). A typical residential uplink is 10–50 Mbps. This saturates at a handful of concurrent photo loads. For personal/family use (2–5 concurrent users) this is acceptable. For any broader sharing, consider moving to a VPS with datacenter bandwidth.

## FortiGate Virtual Server setup

This section covers the FortiGate configuration needed to route two public HTTPS hostnames
(`diary.perfectday.andrewlass.com` and `api.diary.perfectday.andrewlass.com`) to the NUC, with
port translation (WAN:443 → NUC:3000 and NUC:8000 respectively) and automatic TLS via Let's Encrypt.

**Why Virtual Server + Content Routing, not plain port-forward VIPs:** Two hostnames share one
WAN IP and one port (443). Plain port-forward VIPs cannot distinguish between them — you cannot
create two VIPs that both forward WAN:443 to different backend ports. Virtual Server with HTTP
Content Routing reads the decrypted `Host` header and dispatches to the correct Real Server pool.

**Prerequisite:** DNS A records for both hostnames must resolve to your WAN IP before starting
(ACME HTTP-01 challenge requires the domain to point at the FortiGate's WAN interface).

---

### Step 1 — Issue two Let's Encrypt certificates via FortiGate ACME

FortiGate's built-in ACME client issues one certificate per entry (no multi-SAN support in the UI).
Create two separate certs — one per hostname.

In the FortiGate UI, repeat **System → Certificates → Local → Create/Import → Let's Encrypt** twice:

| Certificate name | Domain | Email |
|---|---|---|
| `perfectday-diary-le` | `diary.perfectday.andrewlass.com` | your contact email |
| `perfectday-api-le` | `api.diary.perfectday.andrewlass.com` | your contact email |

FortiGate performs HTTP-01 over port 80 and downloads each signed cert. Each cert renews independently — both will auto-renew as long as port 80 is open.

> **Port 80 must reach the FortiGate WAN interface.** If you have an existing firewall policy blocking
> inbound HTTP, temporarily open port 80 WAN → local before issuing the certs, then close it after.
> The HTTP→HTTPS redirect Virtual Server in Step 4 keeps port 80 open permanently afterward so
> ACME renewals succeed without manual intervention.

---

### Step 2 — Create Real Server pools

Real Servers define the backend targets. FortiGate 7.2 names this object type differently in the
GUI depending on build — look for **Server Load Balance → Real Servers** or configure via CLI.

**Via CLI (most reliable across 7.2 builds):**

```
config firewall ldb-monitor
    edit "nuc-http-check"
        set type http
        set port 3000
        set http-get "/"
    next
end

config firewall server-load-balance
    edit "nuc-web"
        set type ip
        config realservers
            edit 1
                set ip <NUC_LAN_IP>
                set port 3000
                set monitor nuc-http-check
            next
        end
    next
    edit "nuc-api"
        set type ip
        config realservers
            edit 1
                set ip <NUC_LAN_IP>
                set port 8000
            next
        end
    next
end
```

Replace `<NUC_LAN_IP>` with the NUC's LAN IP (e.g., `192.168.1.x`).

---

### Step 3 — Create the HTTPS Virtual Server on WAN:443

This is the main listener. FortiGate terminates TLS here and forwards plain HTTP to the backend.
Because the two hostnames have separate certs, the Virtual Server uses the `diary.*` cert as the
listener-level default, and the Content Routing rule for `api.*` overrides with its own cert.

**Via GUI:** Policy & Objects → Virtual IPs → New

| Field | Value |
|---|---|
| Name | `perfectday-https` |
| Type | Virtual Server |
| External interface | WAN |
| External IP | `<WAN_IP>` |
| External service port | 443 |
| Virtual server type | HTTPS |
| Server SSL certificate | `perfectday-diary-le` (default — used for `diary.*` requests) |
| HTTP content routing | Enable |
| Default server pool | `nuc-web` |

**Add two HTTP Content Routing rules** (evaluated top-to-bottom):

| # | Match type | Value | SSL cert override | Action / Pool |
|---|---|---|---|---|
| 1 | Host | `api.diary.perfectday.andrewlass.com` | `perfectday-api-le` | Forward to `nuc-api` |
| 2 | Host | `diary.perfectday.andrewlass.com` | `perfectday-diary-le` | Forward to `nuc-web` |

> Rule order matters: place the API rule first because it is more specific.
> The default pool (`nuc-web`) handles any request whose `Host` header matches neither rule.
>
> If the GUI does not expose a per-rule SSL cert field in your 7.2 build, set the listener cert to
> `perfectday-diary-le` and leave the API rule without a cert override — the client will receive the
> `diary.*` cert for `api.*` requests. This is a cert mismatch warning in the browser but does not
> break TLS or OAuth. The correct fix is to use a single cert covering both SANs (if you get a
> newer FortiOS build that supports multi-domain ACME) or to split into two separate Virtual Servers
> on different ports and use a port-based redirect instead.

---

### Step 4 — Create the HTTP redirect Virtual Server on WAN:80

This redirects all HTTP traffic to HTTPS and also handles ACME HTTP-01 renewals (FortiGate
intercepts `/.well-known/acme-challenge/` automatically before the redirect fires).

**Via GUI:** Policy & Objects → Virtual IPs → New

| Field | Value |
|---|---|
| Name | `perfectday-http-redirect` |
| Type | Virtual Server |
| External interface | WAN |
| External IP | `<WAN_IP>` |
| External service port | 80 |
| Virtual server type | HTTP |
| HTTP to HTTPS redirect | Enable |

---

### Step 5 — Create firewall policies

Two policies are needed: one for HTTPS, one for HTTP.

**Via GUI:** Policy & Objects → IPv4 Policy → New

| Policy | Incoming interface | Outgoing interface | Destination | Service | Action |
|---|---|---|---|---|---|
| `perfectday-https-in` | WAN | Virtual server zone | `perfectday-https` VIP | HTTPS | ACCEPT |
| `perfectday-http-in` | WAN | Virtual server zone | `perfectday-http-redirect` VIP | HTTP | ACCEPT |

Enable **NAT** on both policies (FortiGate rewrites the source IP when forwarding to the backend).

---

### Step 6 — Verify

After saving all the above, test from **off-network** (mobile hotspot, not the home LAN):

```bash
# HTTPS routing — web app:
curl -I https://diary.perfectday.andrewlass.com/healthz
# Expect: HTTP/2 200

# HTTPS routing — API:
curl -I https://api.diary.perfectday.andrewlass.com/healthz
# Expect: HTTP/2 200, body {"status":"ok"}

# HTTP → HTTPS redirect:
curl -I http://diary.perfectday.andrewlass.com/
# Expect: 301 → https://diary.perfectday.andrewlass.com/

# Certificate issuer and SAN:
openssl s_client -connect diary.perfectday.andrewlass.com:443 \
  -servername diary.perfectday.andrewlass.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# Expect: issuer = Let's Encrypt, not-after ≥ 60 days from today,
#         subject CN or SAN includes both hostnames.

# Confirm SNI routing (both hosts on the same WAN IP):
curl -sk --resolve "diary.perfectday.andrewlass.com:443:<WAN_IP>" \
  https://diary.perfectday.andrewlass.com/healthz
# Expect: 200 from Next.js

curl -sk --resolve "api.diary.perfectday.andrewlass.com:443:<WAN_IP>" \
  https://api.diary.perfectday.andrewlass.com/healthz
# Expect: 200 from FastAPI
```

---

## Single point of failure

Power outage, ISP outage, disk failure, OS reboot, or accidental power loss all take the diary offline. There is no HA story on this deployment. Mitigations:

- UPS on the NUC
- External backup (Backblaze B2) for data recovery after disk failure
- `restart: always` on all Docker Compose services for process recovery after reboot
- Quarterly DR drill (above) to verify recovery is actually possible

If reliability is a hard requirement, the hybrid topology adds a Hetzner CX21 cloud edge + Postgres streaming read-replica that keeps the diary readable when the NUC is offline — see [`deploy/hybrid.md`](hybrid.md).

Appropriate for personal/family use. Not appropriate for any user beyond the household.
