# Home-Lab Deployment (Intel NUC)

This document captures guidance specific to deploying Perfect Day on the home-lab NUC. The application architecture is host-agnostic — see `design/01-architecture.md`. Everything here is deployment-specific.

---

## Hardware

- Intel NUC 4-core x86 1.85 GHz, 8 GB RAM
- Shared with other household services
- Single machine — no high availability

## Edge

- **Cloudflare proxy (orange cloud):** public-facing TLS (browser ↔ CF) via Cloudflare Universal SSL — auto-renewed by CF, no operator action needed.
- **FortiGate 7.2+:** CF↔origin TLS termination via a Cloudflare Origin Certificate, IPS and antivirus (policy 35's profiles; no WAF profile today). Forwards plain HTTP to NUC on the LAN.
- One Cloudflare Origin Certificate for `*.andrewlass.com, andrewlass.com`, generated via CSR on FortiGate (private key never leaves the device). **Expires 2027-11-16**; see § FortiGate Virtual Server setup, Step 1.
- Planned, not built: a FortiGate WAF rule so `perfectday-media` accepts PUT only (uploads). Depends on P-10.
- CORS allowlist on the API for the web origin; Expo dev tunnel allowed only when `ENV=dev`
- **Hybrid mode:** in the hybrid topology, the NUC's FortiGate vhost for `perfectday.andrewlass.com` is deactivated. The NUC is reachable only over WireGuard (or LAN). DNS A records point to the CX21, which handles all public TLS. See [`deploy/hybrid.md`](hybrid.md).

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

This is the configuration as built and verified on 2026-11-16 (PoC 25.6, finding P-9 in
[`design/code-review-2026-11.md`](../design/code-review-2026-11.md)).

**Hostnames.** Every public name has exactly one label before `andrewlass.com`, because
Cloudflare's free Universal SSL covers "the root domain … and first-level subdomains" only
([Cloudflare](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/)).
The old `diary.perfectday.andrewlass.com` names were never covered, and Cloudflare reset every
handshake to them.

| Hostname | Caddy routes it to |
|---|---|
| `perfectday.andrewlass.com` | `web:3000` (Next.js) |
| `perfectday-api.andrewlass.com` | `api:8000` (FastAPI) |
| `perfectday-media.andrewlass.com` | nothing yet (DNS record only; P-10 decides how photo links work) |

**TLS path.** Browser → Cloudflare (Universal SSL, `andrewlass.com, *.andrewlass.com`) →
FortiGate (Cloudflare Origin Certificate, decrypts, runs IPS/AV) → plain HTTP on the LAN →
Caddy on the NUC, port 80, which routes by `Host`. Cloudflare's encryption mode for the zone is
**Full (strict)**, which accepts an Origin CA certificate
([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)).
The zone mode is zone-wide; do not change it, because other `andrewlass.com` sites share the zone.

**FortiGate objects (names as configured):**

| Object | Name |
|---|---|
| Origin certificate | `perfectday-cf-origin-2026` |
| Health check | `perfectday-caddy-tcp` |
| Virtual server (VIP) | `perfectday-https-cf-origin` |
| Firewall policy | 35, `perfectday-https-cf-origin` |
| SSL/SSH inspection profile on the policy | `perfectday-cf-origin` (contents not reviewed) |

---

### Step 1 — Origin certificate

FortiGate generates the key pair and a CSR; Cloudflare signs it. The private key never leaves
the FortiGate.

1. **FortiGate:** System → Certificates → Create/Import → Generate CSR.

   | Field | Value |
   |---|---|
   | Certificate Name | `perfectday-cf-origin-2026` |
   | Common Name | `andrewlass.com` |
   | Subject Alternative Name | `DNS:*.andrewlass.com,DNS:andrewlass.com` |
   | Key | RSA 2048 |

   The wildcard covers any first-level name, so adding `perfectday-media` routes later needs no
   new certificate. A wildcard covers one level only
   ([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)). The
   cost: this key is accepted by Cloudflare for any `andrewlass.com` subdomain on the
   Cloudflare→FortiGate hop, which matters only if the FortiGate itself is compromised.

2. **Cloudflare:** SSL/TLS → Origin Server → Create Certificate → use my private key and CSR.
   Paste the CSR, and set the hostnames field to `*.andrewlass.com, andrewlass.com` as well
   (Cloudflare's docs do not say whether the CSR or the field decides the names, so set both).

3. **Check the PEM before importing:**
   ```bash
   openssl x509 -in cert.pem -noout -ext subjectAltName -enddate
   # Expect: DNS:*.andrewlass.com, DNS:andrewlass.com
   ```

4. **FortiGate:** import the PEM into the pending CSR. It becomes an active local certificate.

> **Expiry:** the certificate in use expires **2027-11-16** (issued for one year).
> Cloudflare does not send expiry notices for Origin CA certificates
> ([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)).
> When it expires, every request fails with Cloudflare error 526. Renew it before then by
> repeating this step.

---

### Step 2 — Health check

```
config firewall ldb-monitor
    edit "perfectday-caddy-tcp"
        set type tcp
        set port 80
        set interval 10
        set timeout 2
        set retry 3
    next
end
```

A TCP check only proves Caddy accepts connections on port 80. The earlier HTTP check
(`perfectday-caddy-http`: `GET /healthz`, match "Not Found") never marked the NUC up, so the VIP
had no healthy server and Cloudflare returned 522. The likely cause, not yet confirmed from
Caddy's log: if the FortiGate's probe is HTTP/1.1 without a `Host` header, Caddy (Go's HTTP server)
answers `400 Bad Request: missing required Host header` before any route runs, so no Caddyfile
route can fix it. Caddy's access log will not show such a request, so confirm it on the
FortiGate with a short capture of the probe and Caddy's reply:
`diagnose sniffer packet any 'host <NUC_LAN_IP> and port 80' 6 10`.

---

### Step 3 — Virtual server (VIP)

```
config firewall vip
    edit "perfectday-https-cf-origin"
        set type server-load-balance
        set extip <WAN_IP>
        set extintf "wan1"
        set server-type https
        set extport 443
        set ssl-certificate "perfectday-cf-origin-2026"
        set monitor "perfectday-caddy-tcp"
        config realservers
            edit 1
                set ip <NUC_LAN_IP>
                set port 80
            next
        end
    next
end
```

One real server on port 80: the FortiGate forwards decrypted HTTP to Caddy, and Caddy routes by
`Host`.

> **Open risk: `extip` is the WAN IP, which Comcast can change.** DDNS updates the DNS records,
> but not this VIP, so after a lease change Cloudflare would reach the new IP and no VIP would
> match. Whether a server-load-balance VIP accepts `0.0.0.0` (any address on `wan1`) is not
> confirmed. Until it is, update `extip` after an IP change.

> **Watch for other VIPs on WAN:443.** On 2026-11-16 three VIPs claimed it: this one, a plain
> port-forward (`perfectday-nuc`, WAN:443 → NUC:80) and `blacknight-ssl-offload` (another
> service). Policy 35 used the port-forward, so Cloudflare's TLS hello reached Caddy's
> plain-HTTP port and Cloudflare returned 525. After policy 35 was pointed at this VIP, the site
> worked with `blacknight-ssl-offload` still defined; which VIP wins when several share an IP and
> port, and whether an unused one can interfere, is not established. Forum reports say two
> TLS-terminating VIPs cannot share one IP and port, and a VIP takes one certificate
> ([Fortinet community](https://community.fortinet.com/t5/Support-Forum/Two-services-with-same-port/m-p/91360),
> [SNI thread](https://community.fortinet.com/t5/Support-Forum/Fortigate-SSL-Offloading-with-SNI/td-p/252027));
> this is not in Fortinet's documentation. Delete the port-forward VIP. If another service must
> keep WAN:443, move Perfect Day to another external port and add a Cloudflare Origin Rule that
> overrides the destination port (available on the Free plan:
> [Cloudflare](https://developers.cloudflare.com/rules/origin-rules/)).

---

### Step 4 — Firewall policy

```
config firewall policy
    edit 35
        set name "perfectday-https-cf-origin"
        set srcintf "wan1"
        set dstintf "<LAN_INTERFACE>"
        set action accept
        set srcaddr "all"
        set dstaddr "perfectday-https-cf-origin"
        set schedule "always"
        set service "ALL"
        set inspection-mode proxy
        set utm-status enable
        set ssl-ssh-profile "perfectday-cf-origin"
        set av-profile "default"
        set ips-sensor "default"
        set logtraffic all
        set nat enable
    next
end
```

- **`inspection-mode proxy` is required.** Fortinet's 7.2 guide says a flow-based policy accepts
  only TCP/UDP/IP virtual servers, so an HTTPS virtual server needs a proxy-based policy
  ([FortiOS 7.2.10](https://docs.fortinet.com/document/fortigate/7.2.10/administration-guide/713497/virtual-server-load-balance)).
  Flow is the default and `show` does not print it, so a missing `inspection-mode` line means
  flow-based.
- **Open item: restrict `srcaddr` to Cloudflare.** Make an address group from
  <https://www.cloudflare.com/ips-v4> and use it instead of `all`, so only Cloudflare's edge can
  reach the origin.
- **Port 80 inbound is not needed.** Cloudflare redirects HTTP to HTTPS at its edge.

---

### Step 5 — Verify

```bash
# Through Cloudflare. Before the app is deployed with the new names, Caddy answers "Not Found"
# (404); that still proves the whole Cloudflare -> FortiGate -> Caddy path works.
curl -s -o /dev/null -w "%{http_code}\n" https://perfectday.andrewlass.com/
curl -s https://perfectday-api.andrewlass.com/readyz
# Expect after deploy: the web page (not Caddy's "Not Found"), and readyz 200.

# The edge certificate browsers see:
openssl s_client -connect perfectday.andrewlass.com:443 -servername perfectday.andrewlass.com \
  </dev/null 2>/dev/null | openssl x509 -noout -issuer -ext subjectAltName
# Expect: Google Trust Services (or Let's Encrypt); DNS:andrewlass.com, DNS:*.andrewlass.com

# The next two checks connect straight to the WAN IP. Run them from off-network (e.g. a phone
# hotspot): from the LAN, a hairpin path may use a different policy than Cloudflare's traffic.
# The origin certificate:
openssl s_client -connect <WAN_IP>:443 -servername perfectday.andrewlass.com \
  </dev/null 2>/dev/null | openssl x509 -noout -issuer -ext subjectAltName -enddate
# Expect: CloudFlare Origin SSL Certificate Authority; DNS:*.andrewlass.com, DNS:andrewlass.com

# Plain HTTP on 443 must not reach Caddy (that would mean a port-forward is in the path):
curl -s -i --max-time 8 -H "Host: perfectday.andrewlass.com" http://<WAN_IP>:443/
# Expect: no response

# On the FortiGate:
#   diagnose firewall vip realserver list    -> status=up alive=1
```

**Cloudflare error codes seen while building this, and what they meant:**

| Error | Meaning | Cause on 2026-11-16 |
|---|---|---|
| 525 | TLS handshake to the origin failed | Policy 35 used the plain port-forward VIP; nothing decrypted |
| 522 | Cloudflare could not open a connection | VIP had no healthy real server (HTTP health check failing) |
| 526 | Origin certificate invalid for the name | Not seen; expect it if the origin certificate expires or lacks the name |

To trace one connection on the FortiGate:

```
diagnose debug reset
diagnose debug flow filter dport 443
diagnose debug flow show function-name enable
diagnose debug flow trace start 20
diagnose debug enable
# load the site, then:
diagnose debug disable
diagnose debug reset
```

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
