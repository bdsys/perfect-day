# Home-Lab Deployment (Intel NUC)

This document captures guidance specific to deploying Perfect Day on the home-lab NUC. The application architecture is host-agnostic — see `design/01-architecture.md`. Everything here is deployment-specific.

---

## Hardware

- Intel NUC 4-core x86 1.85 GHz, 8 GB RAM
- Shared with other household services
- Single machine — no high availability

## Edge

- **Cloudflare proxy (orange cloud):** public-facing TLS (browser ↔ CF) via Cloudflare Universal SSL — auto-renewed by CF, no operator action needed.
- **FortiGate 7.2+:** CF↔origin TLS termination via a Cloudflare Origin Certificate, WAF/IPS, virtual hosting. Forwards plain HTTP to NUC on the LAN.
- One Cloudflare Origin Certificate (15-year validity) covering all three planned subdomains as SANs: `diary.perfectday.andrewlass.com`, `api.diary.perfectday.andrewlass.com`, and `media.diary.perfectday.andrewlass.com`. Generated once via CSR on FortiGate — private key never leaves the device.
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
port translation (WAN:443 → NUC:3000 and NUC:8000 respectively).

**TLS architecture:** Cloudflare proxy is **on** (orange cloud) for all three subdomains. Cloudflare
terminates the public-facing TLS (browser ↔ CF) and forwards to FortiGate over a separate TLS hop
(CF ↔ FortiGate) using a Cloudflare Origin Certificate installed on the FortiGate. FortiGate
decrypts that second hop and forwards plain HTTP to the NUC on the LAN.

**Prerequisite:** Cloudflare proxy must be **on** (orange cloud) for the three A records in your
Cloudflare DNS dashboard before starting. See [`deploy/cloudflare.md`](cloudflare.md) § DNS and
§ Cloudflare Origin Certificate setup.

---

### Step 1 — Generate a CSR on FortiGate and obtain a Cloudflare Origin Certificate

FortiGate generates the keypair and produces a CSR. You submit the CSR to Cloudflare, which signs
it with the Cloudflare Origin CA. The private key never leaves FortiGate.

**Via FortiGate UI:** System → Certificates → Create/Import → Certificate → Generate CSR

| Field | Value |
|---|---|
| Certificate Name | `perfectday-cf-origin` |
| Subject → Common Name (CN) | `diary.perfectday.andrewlass.com` |
| Subject Alternative Name | `DNS:diary.perfectday.andrewlass.com,DNS:api.diary.perfectday.andrewlass.com,DNS:media.diary.perfectday.andrewlass.com` |
| Key Type | RSA |
| Key Size | 2048 |
| Enrollment Method | File Based |

Click OK. Download the resulting `.csr` file.

**Verify the CSR has all three SANs before submitting:**

```bash
openssl req -in perfectday-cf-origin.csr -noout -text | grep -A1 "Subject Alternative Name"
# Expect: DNS:diary.perfectday.andrewlass.com, DNS:api.diary.perfectday.andrewlass.com, DNS:media.diary.perfectday.andrewlass.com
```

**Submit to Cloudflare:** Dashboard → SSL/TLS → Origin Server → Create Certificate.
- Uncheck **"Generate private key and CSR with Cloudflare"**.
- Paste the entire `-----BEGIN CERTIFICATE REQUEST-----` … `-----END CERTIFICATE REQUEST-----` block.
- Validity: **15 years**.
- Click Create. Copy the signed certificate PEM — it is shown only once.

**Install on FortiGate:** System → Certificates → find `perfectday-cf-origin` (Pending CSR state)
→ Import → Local Certificate. Upload the signed `.crt` file. The cert moves to active state.

**Set Cloudflare SSL/TLS mode to Full (strict):** Dashboard → SSL/TLS → Overview →
Encryption mode → **Full (strict)**. This ensures CF validates the Origin Cert chain before
forwarding to your origin.

---

### Step 2 — Create the HTTPS Virtual Server on WAN:443

In FortiOS 7.2, a `firewall vip` with `type server-load-balance` and `server-type https` terminates
the Cloudflare↔origin TLS hop (using the Origin Cert from Step 1) and forwards decrypted traffic
to a single backend. All Host-header routing happens inside Caddy on the NUC — FortiGate only needs
one realserver entry pointing at the Caddy edge on port 80.

**Via CLI:**

First, create the HTTP health-check monitor that probes the Caddy edge:

```
config firewall ldb-monitor
    edit "perfectday-caddy-http"
        set type http
        set port 80
        set http-get "/healthz"
        set http-match "Not Found"
        set interval 10
        set timeout 2
        set retry 3
    next
end
```

Then create the VIP and bind the monitor at the VIP level (not inside `config realservers` — when
a top-level `monitor` is bound to a `server-load-balance` VIP, FortiGate health-checks every
realserver in the pool against that monitor automatically):

```
config firewall vip
    edit "perfectday-https"
        set type server-load-balance
        set extip <WAN_IP>
        set extintf "wan1"
        set extport 443
        set server-type https
        set ssl-certificate "perfectday-cf-origin"
        set monitor "perfectday-caddy-http"
        config realservers
            edit 1
                set ip <NUC_LAN_IP>
                set port 80
            next
        end
    next
end
```

Replace `<WAN_IP>` with your FortiGate WAN IP and `<NUC_LAN_IP>` with the NUC's LAN IP.

> **Why match "Not Found":** Caddy's catch-all returns `404 Not Found` for `/healthz` (the probe
> arrives without a `Host` header that matches `@diary` or `@api`). A 404 with that body proves
> Caddy is alive and routing — exactly what we want to verify. We're checking Caddy itself, not
> the backends. Without this monitor, FortiGate has no way to know if Caddy crashed, and would
> keep forwarding traffic into a black hole.

> **`set extintf`**: substitute your actual WAN interface name if it differs from `wan1`
> (check with `get system interface` — look for the interface with your WAN IP).

> **One realserver, port 80:** FortiGate forwards decrypted HTTP to the Caddy edge container on
> the NUC. Caddy reads the `Host` header and routes to `web:3000` or `api:8000` over the Docker
> internal network. This is necessary because FortiGate's `firewall vip` does not support
> Host-header routing — that feature is exclusive to FortiADC, a separate product.

**Via GUI (if preferred):** Policy & Objects → Virtual IPs → New

| Field | Value |
|---|---|
| Name | `perfectday-https` |
| Type | Server Load Balance |
| External interface | WAN (`wan1`) |
| External IP | `<WAN_IP>` |
| External service port | 443 |
| Virtual server type | HTTPS |
| Server SSL certificate | `perfectday-cf-origin` |
| Health Check | `perfectday-caddy-http` (create under Policy & Objects → Health Check first) |

Add one real server:

| Real server | IP | Port |
|---|---|---|
| 1 | `<NUC_LAN_IP>` | 80 |

The health check is bound at the VIP level, not on the real server. FortiGate uses it to probe
every realserver in the pool automatically.

---

### NUC-side Caddy edge

The Caddy container handles Host-header routing after FortiGate decrypts the traffic. See
[`deploy/caddy/Caddyfile.tmpl`](caddy/Caddyfile.tmpl) for the routing rules and
[`deploy/caddy/README.md`](caddy/README.md) for local debugging instructions.

The Caddy service starts automatically when deploying with `--profile nuc`:

```bash
docker compose --profile nuc up -d
```

To bring up only the edge service:

```bash
docker compose --profile nuc up -d edge
```

---

### Step 3 — Create firewall policies

Two policies are needed: one for HTTPS inbound from Cloudflare, and one to lock down inbound
traffic to Cloudflare IP ranges only.

**Via GUI:** Policy & Objects → IPv4 Policy → New

| Policy | Incoming interface | Outgoing interface | Destination | Service | Action |
|---|---|---|---|---|---|
| `perfectday-https-in` | WAN | Virtual server zone | `perfectday-https` VIP | HTTPS | ACCEPT |

Enable **NAT** on the policy (FortiGate rewrites the source IP when forwarding to the backend).

> **Restrict to Cloudflare IPs (recommended):** Create an address group containing
> [Cloudflare's published IP ranges](https://www.cloudflare.com/ips/) and use it as the
> source address on `perfectday-https-in` instead of `all`. This ensures FortiGate only
> accepts origin traffic from Cloudflare's edge — direct connections to your WAN IP are
> blocked at the firewall even if an attacker knows it. Cloudflare publishes its IP ranges
> at `https://www.cloudflare.com/ips/` and updates them infrequently; review quarterly.

> **HTTP (port 80) inbound is not needed.** Cloudflare handles HTTP→HTTPS redirects at
> its edge before traffic reaches your WAN. Do not open port 80 on FortiGate.

---

### Step 4 — Verify

After saving all the above, test from **off-network** (mobile hotspot, not the home LAN):

```bash
# HTTPS routing — web app (through Cloudflare):
curl -I https://diary.perfectday.andrewlass.com/healthz
# Expect: HTTP/2 200

# HTTPS routing — API (through Cloudflare):
curl -I https://api.diary.perfectday.andrewlass.com/healthz
# Expect: HTTP/2 200, body {"status":"ok"}

# Public-facing cert is Cloudflare Universal SSL (not the Origin Cert):
openssl s_client -connect diary.perfectday.andrewlass.com:443 \
  -servername diary.perfectday.andrewlass.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# Expect: issuer = Let's Encrypt or Google Trust Services (CF's Universal SSL provider),
#         not-after ≥ 60 days from today. This is the cert browsers see.

# Origin cert on FortiGate — bypass Cloudflare and hit the WAN IP directly with SNI:
openssl s_client -connect <WAN_IP>:443 \
  -servername diary.perfectday.andrewlass.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# Expect: issuer = CloudFlare Origin SSL Certificate Authority, validity ~15 years,
#         SANs include all three planned hostnames.

# Confirm CF is in the path (A record resolves to Cloudflare anycast, not your WAN IP):
dig +short diary.perfectday.andrewlass.com
# Expect: Cloudflare anycast IPs (e.g., 104.16.x.x or 172.64.x.x), NOT your home WAN IP.

# Confirm Host-header routing (FortiGate → Caddy → backends):
# Both subdomains resolve through the same VIP but Caddy dispatches to different backends.
curl -sk --resolve "diary.perfectday.andrewlass.com:443:<WAN_IP>" \
  https://diary.perfectday.andrewlass.com/healthz
# Expect: 200 from Next.js

curl -sk --resolve "api.diary.perfectday.andrewlass.com:443:<WAN_IP>" \
  https://api.diary.perfectday.andrewlass.com/healthz
# Expect: 200 {"status":"ok"} from FastAPI
```

> **Note on direct WAN tests:** With Cloudflare proxy on, `dig` returns CF anycast IPs, not your
> home WAN IP. The `--resolve` flag above forces curl to bypass DNS and connect directly to FortiGate's
> WAN IP so you can verify FortiGate's routing independently of Cloudflare. Substitute your actual WAN IP.

---

> **Future e2e TLS to NUC backends:** The FortiGate→NUC hop is intentionally plain HTTP on the
> home LAN (trusted segment). If full end-to-end TLS is ever required, drop a small reverse-proxy
> container (e.g., Caddy with `tls internal`) into `docker-compose.yml` on port `8443`, update the
> FortiGate Real Server pool to target `:8443`, and set the realserver SSL mode to `full`. This is a
> config-only change — no application code changes required. Port `8443` is reserved in `PORTS.md`
> for this purpose.

## Single point of failure

Power outage, ISP outage, disk failure, OS reboot, or accidental power loss all take the diary offline. There is no HA story on this deployment. Mitigations:

- UPS on the NUC
- External backup (Backblaze B2) for data recovery after disk failure
- `restart: always` on all Docker Compose services for process recovery after reboot
- Quarterly DR drill (above) to verify recovery is actually possible

If reliability is a hard requirement, the hybrid topology adds a Hetzner CX21 cloud edge + Postgres streaming read-replica that keeps the diary readable when the NUC is offline — see [`deploy/hybrid.md`](hybrid.md).

Appropriate for personal/family use. Not appropriate for any user beyond the household.
