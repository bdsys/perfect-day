# Cloudflare Setup

This document covers every place Cloudflare is used in Perfect Day, how to
configure each, and the step-by-step setup sequence.

---

## What Cloudflare is used for

| Use | Deployment target | Cost |
|---|---|---|
| Authoritative DNS for `perfectday.bdsys.net` (subdomain delegation) | All | Free |
| DDNS — keeps A records tracking the Comcast dynamic WAN IP | NUC (home-lab) | Free |
| R2 object storage — encrypted photo chunks | Hybrid (NUC + CX21) | Free at PoC scale |

Cloudflare is **not** used as a reverse proxy / CDN for this project (FortiGate
on the NUC and Caddy on the CX21 handle TLS termination). You can optionally
enable the Cloudflare proxy ("orange cloud") on the A records — see the note
in § 1 below — but it is not required.

---

## One-time account setup

1. Create a free Cloudflare account at cloudflare.com.
2. Add the zone `bdsys.net` to Cloudflare. Cloudflare will ask you to
   change the authoritative nameservers for the entire zone, but **do not
   do that** — see § 1 below for why you only delegate a subdomain.
3. Note the **Account ID** from the Cloudflare dashboard → right sidebar.
   You will need it for R2 bucket URLs.

---

## Section 1 — DNS / subdomain delegation

### Background

`bdsys.net` is registered at GoDaddy, with GoDaddy as the authoritative
nameserver. GoDaddy holds all your existing records (MX, SPF, DKIM, etc.).
You do not want to move the entire zone to Cloudflare and risk disrupting
email or other existing services.

Instead, delegate only the `perfectday.bdsys.net` sub-zone to Cloudflare.
GoDaddy remains authoritative for `bdsys.net`; Cloudflare becomes
authoritative for `*.perfectday.bdsys.net`. This is a standard NS
delegation — no migration of existing records required.

### Step 1.1 — Add `perfectday.bdsys.net` as a zone in Cloudflare

Cloudflare's free plan supports full-zone management. You need to add a
zone for `perfectday.bdsys.net` specifically (not `bdsys.net`).

> **Note:** Cloudflare's standard "add a site" flow assumes you own the
> apex domain and want to move all NS there. For a subdomain delegation,
> you may need to contact Cloudflare support or use the "partial zone /
> CNAME setup" option under Enterprise — **or** use the simpler workaround
> below.

**Simpler workaround (no Cloudflare account zone needed):** Use Cloudflare
only for DDNS via the `bdsys.net` zone API. Add `bdsys.net` to Cloudflare
but do **not** change GoDaddy's nameservers. Instead, use Cloudflare in
"partial / secondary DNS" mode — or just use the Cloudflare API directly
to update records without making Cloudflare authoritative at all.

**Practical recommendation for this project:** Keep GoDaddy authoritative.
Use Cloudflare only for DDNS via its API (a scoped API token updates the A
records in GoDaddy indirectly — see § 2 for the DDNS mechanism). If you
want Cloudflare to actually be authoritative for the subdomains (for the
proxy benefit), the cleanest path is:

1. Add `perfectday.bdsys.net` A records to GoDaddy pointing to the NUC WAN IP.
2. Create a free Cloudflare account and add `bdsys.net` as a zone (Cloudflare
   imports all existing records from GoDaddy via DNS scan).
3. Change GoDaddy's nameservers to Cloudflare's assigned NS servers.
   Cloudflare now serves the entire `bdsys.net` zone including all existing
   MX/SPF/DKIM records it imported.
4. Verify email still works (check MX, SPF, DKIM records were imported
   correctly before cutting over nameservers).

This is a 15-minute operation and the safest path if you eventually want
the Cloudflare proxy. Email records carry over automatically.

### Step 1.2 — Create DNS A records in Cloudflare

Once Cloudflare is authoritative (or even just used for API-based DDNS
updates against GoDaddy), create these records:

| Name | Type | Value | TTL | Proxy |
|---|---|---|---|---|
| `diary.perfectday.bdsys.net` | A | `<current NUC WAN IP>` | 300 | Off (grey cloud) for PoC |
| `api.diary.perfectday.bdsys.net` | A | `<current NUC WAN IP>` | 300 | Off for PoC |
| `media.diary.perfectday.bdsys.net` | A | `<current NUC WAN IP>` | 300 | Off for PoC |

TTL 300s (5 min) during PoC allows the DDNS updater to converge within ~10 min
of a Comcast lease change. Raise to 3600s before public launch.

**On the Cloudflare proxy ("orange cloud"):** Enabling the proxy hides your
real home IP address from the internet — connections terminate at Cloudflare's
edge and are forwarded to your NUC. This is a meaningful privacy benefit for a
home server. The trade-off is that TLS terminates at Cloudflare (Cloudflare
sees plaintext), and the FortiGate sees Cloudflare's IP range rather than the
real client IP (fixable with Cloudflare's `CF-Connecting-IP` header).

For PoC, leave proxy **off** (grey cloud) to keep the network path simple. The
FortiGate handles TLS directly. Revisit before public launch.

---

## Section 2 — DDNS (dynamic Comcast IP)

The NUC has a Comcast residential connection with a dynamic public IPv4. The
A records above must track the current WAN IP automatically.

See [`design/dns-and-email.md`](../design/dns-and-email.md) § Dynamic DNS for
the full background and alternatives considered. This section is the
step-by-step setup.

### Step 2.1 — Create a scoped API token

In the Cloudflare dashboard:

1. **My Profile → API Tokens → Create Token**
2. Use the "Edit zone DNS" template.
3. Scope: **Zone Resources → Include → Specific zone → `bdsys.net`** (or
   `perfectday.bdsys.net` if you delegated only the subdomain).
4. Permissions: `Zone.DNS:Edit` only. Nothing else.
5. Create the token and copy it — it is shown only once.
6. Store it on the NUC at `/etc/perfect-day/cloudflare-ddns.token`, mode 0600:
   ```bash
   sudo mkdir -p /etc/perfect-day
   echo "YOUR_TOKEN_HERE" | sudo tee /etc/perfect-day/cloudflare-ddns.token
   sudo chmod 0600 /etc/perfect-day/cloudflare-ddns.token
   ```

### Step 2.2 — Deploy the DDNS updater

**Option A — FortiGate built-in (preferred if supported)**

FortiOS 7.4 has a built-in DDNS client under **Network → DNS → Dynamic DNS**.
If Cloudflare is listed as a provider, configure it there with the API token
above. This removes the need for any script on the NUC. Verify against your
FortiOS 7.4 config — provider support varies by firmware version.

**Option B — `cloudflare-ddns` Docker container on the NUC**

[`timothymiller/cloudflare-ddns`](https://github.com/timothymiller/cloudflare-ddns)
is a lightweight Python container that polls your WAN IP and updates
Cloudflare A records.

Add to `docker-compose.yml`:

```yaml
  cloudflare-ddns:
    image: timothymiller/cloudflare-ddns:latest
    restart: unless-stopped
    environment:
      TZ: America/Chicago
    volumes:
      - /etc/perfect-day/cloudflare-ddns.token:/app/config.json:ro
    # config.json format — see Step 2.3
```

**Option C — `ddclient` (Perl, in Debian/Ubuntu repos)**

```bash
sudo apt-get install ddclient
```

`/etc/ddclient.conf`:
```
daemon=300
syslog=yes
protocol=cloudflare
use=web, web=api.ipify.org
login=token
password=<your-token>
zone=bdsys.net
diary.perfectday.bdsys.net,api.diary.perfectday.bdsys.net,media.diary.perfectday.bdsys.net
```

```bash
sudo systemctl enable ddclient
sudo systemctl start ddclient
```

### Step 2.3 — Configure the updater

For `timothymiller/cloudflare-ddns`, the `config.json` mounted above:

```json
{
  "cloudflare": [
    {
      "authentication": {
        "api_token": "<your-token-here>"
      },
      "zone_id": "<zone-id-from-cloudflare-dashboard>",
      "subdomains": [
        { "name": "diary.perfectday" },
        { "name": "api.diary.perfectday" },
        { "name": "media.diary.perfectday" }
      ],
      "proxied": false
    }
  ],
  "a": true,
  "aaaa": false,
  "purgeUnknownRecords": false,
  "ttl": 300
}
```

Zone ID is on the Cloudflare dashboard → your zone → right sidebar.

### Step 2.4 — Verify DDNS is working

```bash
# Current WAN IP from the NUC's perspective:
curl -s https://api.ipify.org

# Current DNS resolution:
dig +short diary.perfectday.bdsys.net

# They should match. If not, check the updater logs:
docker compose logs cloudflare-ddns
# or
sudo journalctl -u ddclient -n 50
```

### Failure mode

If the WAN IP changes and the updater fails to push within 15 minutes, DNS
resolves to a stale address and the site is unreachable. The observability
plan in `design/observability.md` includes a synthetic check for this. Set
an alert on IP-mismatch lasting > 15 min.

---

## Section 3 — R2 photo storage (hybrid deployment only)

R2 is only used in the [hybrid deployment](hybrid.md) (NUC + Hetzner CX21).
In NUC-only mode, photos are stored in local MinIO. Skip this section unless
you are setting up the hybrid topology.

### Background

In hybrid mode, the Hetzner CX21 cloud edge serves photos to end users.
MinIO on the NUC is not publicly reachable in hybrid mode (only accessible
over WireGuard). Cloudflare R2 is S3-compatible object storage with **$0
egress fees**, which makes it the right choice for photo serving from a
cloud edge. See [`deploy/hybrid.md`](hybrid.md) § R2 photo storage for the
full architecture.

### Step 3.1 — Create an R2 bucket

In the Cloudflare dashboard:

1. **R2 → Create bucket**
2. Name: `perfectday-photos` (or similar)
3. Location: Auto (or pick a region closest to your users)
4. Public access: **Disabled** (all reads are proxied through the API —
   see `design/08-security-privacy.md` § MinIO access controls)

### Step 3.2 — Create an R2 API token

1. **R2 → Manage R2 API Tokens → Create API Token**
2. Permissions: **Object Read & Write** on the `perfectday-photos` bucket only.
   Do not grant admin or bucket-level permissions.
3. Copy the **Access Key ID** and **Secret Access Key** — shown only once.
4. Store both in the sops-encrypted secrets file on the NUC and CX21
   (see `design/secrets.md` — entries `r2_access_key` and `r2_secret_key`).

### Step 3.3 — Configure the application

The R2 endpoint URL is:
```
https://<account-id>.r2.cloudflarestorage.com
```

Account ID is in the Cloudflare dashboard → right sidebar.

In your environment config (`.env.production` or sops YAML):

```env
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=<r2-access-key>
S3_SECRET_ACCESS_KEY=<r2-secret-key>
S3_BUCKET_NAME=perfectday-photos
S3_REGION=auto
```

The application already uses `boto3` with `endpoint_url` — this is a config
swap. No code change required. See [`deploy/hybrid.md`](hybrid.md) § R2 photo
storage for the boto3 config block.

### Step 3.4 — Verify R2 is working

```bash
# From the NUC or CX21 — upload a test object:
aws s3 cp /tmp/test.txt s3://perfectday-photos/test.txt \
  --endpoint-url https://<account-id>.r2.cloudflarestorage.com

# Confirm it's there:
aws s3 ls s3://perfectday-photos/ \
  --endpoint-url https://<account-id>.r2.cloudflarestorage.com

# Confirm public access is disabled (should return 403 or 404):
curl -I https://<account-id>.r2.cloudflarestorage.com/perfectday-photos/test.txt

# Clean up:
aws s3 rm s3://perfectday-photos/test.txt \
  --endpoint-url https://<account-id>.r2.cloudflarestorage.com
```

---

## Setup sequence summary

### NUC-only deployment

1. Add `bdsys.net` to Cloudflare (or just create a scoped API token if keeping GoDaddy authoritative)
2. Create the three A records (`diary.*`, `api.diary.*`, `media.diary.*`)
3. Create the scoped `Zone.DNS:Edit` API token
4. Deploy the DDNS updater (FortiGate built-in or Docker container)
5. Verify DNS resolves to current WAN IP

### Hybrid deployment (adds R2)

Steps 1–5 above, then:

6. Create the R2 bucket (`perfectday-photos`, public access disabled)
7. Create the R2 API token (Object R+W on that bucket only)
8. Add `r2_access_key` / `r2_secret_key` / `S3_ENDPOINT_URL` to sops config
   on both NUC and CX21
9. Verify R2 upload/download round-trip works
10. Switch `S3_ENDPOINT_URL` from MinIO local to R2 in production config

---

## Cost summary

| Service | Plan | Monthly cost |
|---|---|---|
| Cloudflare DNS (full zone or API-only) | Free | $0 |
| Cloudflare DDNS (API calls from updater) | Free | $0 |
| R2 storage (first 10 GB free, $0.015/GB after) | Free tier | $0 at PoC scale |
| R2 egress | Always free | $0 |
| **Total** | | **$0** |

See [`deploy/hybrid.md`](hybrid.md) § Cost for the full hybrid cost breakdown
including CX21 (~€6/mo) and Backblaze B2 backup (~$0.01–0.05/mo).
