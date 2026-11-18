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
- Not needed today: uploads and photo reads go through the API (S-7), so `perfectday-media` has no traffic. The old plan (a PUT-only media host) is dropped unless a direct-to-storage upload comes back.
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
| Garage (photo store) | ~15 MB idle (11 MiB measured, 2026-11-18) |
| Next.js SSR (Node) | 150–300 MB |
| **Total** | **~720 MB – 1.3 GB** |

Comfortable on 8 GB. Risk zone: heavy backfill + LLM calls simultaneously → CPU saturation before RAM.

## Celery sizing

Cap Celery worker concurrency at 2 for PoC. If memory pressure appears, switching to a single-process async worker (`arq`) is a drop-in option — task interface is nearly identical and cuts worker RAM ~50%.

## Storage

Garage for photo storage (the `garage_data` volume, `/var/lib/garage` in the container; config `deploy/garage/garage.toml`). It can be bind-mounted to a larger drive without application changes — update the Docker Compose volume mount. MinIO, the old store, stays until row 41's release 2; the switch is [`nuc-ops.md` § B-Storage](nuc-ops.md#b-storage--switch-photo-storage-to-garage).

## Secrets

`master_secret` and OAuth client secrets are stored in a `sops`-encrypted secrets file. Encrypted with a YubiKey (GPG/age backend). At process start:

```bash
sops -d secrets.enc.yaml > /run/secrets/app.env
# process reads /run/secrets/app.env; file exists only in tmpfs
```

This is a **documented compromise** — the decrypted value lives in process memory on the same host as the database and the photo store. It is acceptable for a personal/family-only deployment where physical access to the host is controlled. It is **not** acceptable for any multi-user or cloud deployment — use a managed secret manager there.

Backup private key (`age` keypair for `age`-encrypted `pg_dump`) is stored on a **separate device** (USB key, second machine, or password manager export), not on the NUC.

## Backup

Built 2026-11-18 (tracker row 36). Design:
[`docs/superpowers/specs/2026-11-18-nuc-backups-design.md`](../docs/superpowers/specs/2026-11-18-nuc-backups-design.md).
Commands: [`nuc-ops.md` § B7](nuc-ops.md#b7--backups).

- **What:** every night at 02:17 (`perfect-day-backup.timer`), the database
  (`pg_dump`), every photo object (exported through the S3 interface, so the backup
  doesn't depend on the store's on-disk format, and works the same with MinIO or Garage) and `/etc/perfect-day/` (including
  `MASTER_SECRET`, without which photos and Google tokens can't be decrypted).
- **Encrypted on the NUC** with `age` to `/etc/perfect-day/backup.pub` before anything
  is written. The private key is in Andrew's password manager and in
  `/etc/perfect-day/backup.key` (root, 600, used by the nightly check). It is never
  inside a backup.
- **Checked before it's kept:** each file is decrypted and checked (the dump's
  completion marker, the object count, `app.env` present). Only then does the night
  replace anything.
- **Where:** `/var/backups/perfect-day/nightly/YYYY-MM-DD/` on the NUC, copied to
  the NAS at `rsync://192.168.1.112/Backups/perfect-day/nightly/`. Andrew copies the
  whole `Backups` share to an external drive kept 30 miles away; that is the offsite
  copy.
- **Retention:** the newest 30 nights, plus the earliest night of each of the last
  12 months. On the NAS the rule is applied to everything the NAS holds, so a
  rebuilt machine never trims its history.
- **Known limit (Andrew's call, 2026-11-18):** the NAS's rsync service has no password.
  Anyone on the home network can delete the backups, though not read them. The
  external drive is the cover.
- **Result:** `/var/log/perfect-day/backup-status.json` and `backup.log`. There is no
  alert yet; an admin panel card is tracker row 62.
- **Restore drill:** `scripts/nuc/drill.sh` restores the newest NAS night into a
  throwaway `perfect-day-drill` project (postgres and the photo store the app uses only) and checks it.
  Repeat it whenever the backup or restore changes. A full rebuild on a second
  machine is tracker row 63.

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
| `perfectday-media.andrewlass.com` | nothing (DNS record only; photos go through the API, S-7) |

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
        set extip 0.0.0.0
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

`extip 0.0.0.0` matches any address on `wan1`. The WAN IP is dynamic (Comcast), so a fixed
`extip` would stop matching after a lease change while DDNS moved the DNS records to the new
address. Set to `0.0.0.0` and verified working on 2026-11-16.

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
- **Open item: restrict `srcaddr` to Cloudflare** (backlog row 40). Until it's done, anyone who
  knows the WAN IP can reach the origin directly; `dev.api.blacknight.bdsys.net` publishes it.
  The commands, from Cloudflare's list as of 2026-11-16 (<https://www.cloudflare.com/ips-v4>,
  15 ranges; regenerate them if the list changes):

  ```
  config firewall address
      edit "cf-173-245-48-0-20"
          set subnet 173.245.48.0/20
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-103-21-244-0-22"
          set subnet 103.21.244.0/22
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-103-22-200-0-22"
          set subnet 103.22.200.0/22
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-103-31-4-0-22"
          set subnet 103.31.4.0/22
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-141-101-64-0-18"
          set subnet 141.101.64.0/18
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-108-162-192-0-18"
          set subnet 108.162.192.0/18
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-190-93-240-0-20"
          set subnet 190.93.240.0/20
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-188-114-96-0-20"
          set subnet 188.114.96.0/20
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-197-234-240-0-22"
          set subnet 197.234.240.0/22
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-198-41-128-0-17"
          set subnet 198.41.128.0/17
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-162-158-0-0-15"
          set subnet 162.158.0.0/15
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-104-16-0-0-13"
          set subnet 104.16.0.0/13
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-104-24-0-0-14"
          set subnet 104.24.0.0/14
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-172-64-0-0-13"
          set subnet 172.64.0.0/13
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
      edit "cf-131-0-72-0-22"
          set subnet 131.0.72.0/22
          set comment "Cloudflare IPv4 (cloudflare.com/ips-v4, 2026-11-16)"
      next
  end
  config firewall addrgrp
      edit "cloudflare-ipv4"
          set member "cf-173-245-48-0-20" "cf-103-21-244-0-22" "cf-103-22-200-0-22" "cf-103-31-4-0-22" "cf-141-101-64-0-18" "cf-108-162-192-0-18" "cf-190-93-240-0-20" "cf-188-114-96-0-20" "cf-197-234-240-0-22" "cf-198-41-128-0-17" "cf-162-158-0-0-15" "cf-104-16-0-0-13" "cf-104-24-0-0-14" "cf-172-64-0-0-13" "cf-131-0-72-0-22"
          set comment "Only Cloudflare may reach the origin (policy 35)"
      next
  end
  config firewall policy
      edit 35
          set srcaddr "cloudflare-ipv4"
      next
  end
  ```

  Check it from off-network (a phone on cellular): `https://<WAN_IP>/` should time out, and
  `https://perfectday.andrewlass.com` should still work.
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
