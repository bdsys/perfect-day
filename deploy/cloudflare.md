# Cloudflare Setup

This document covers every place Cloudflare is used in Perfect Day, how to
configure each, and the step-by-step setup sequence.

---

## What Cloudflare is used for

| Use | Deployment target | Cost |
|---|---|---|
| Authoritative DNS for `perfectday.andrewlass.com` (subdomain delegation) | All | Free |
| DDNS — keeps A records tracking the Comcast dynamic WAN IP | NUC (home-lab) | Free |
| R2 object storage — encrypted photo chunks | Hybrid (NUC + CX21) | Free at PoC scale |

Cloudflare is **not** used as a reverse proxy / CDN for this project (FortiGate
on the NUC and Caddy on the CX21 handle TLS termination). You can optionally
enable the Cloudflare proxy ("orange cloud") on the A records — see the note
in § 1 below — but it is not required.

---

## One-time account setup

1. Create a free Cloudflare account at cloudflare.com.
2. Add the zone `andrewlass.com` to Cloudflare. Cloudflare will ask you to
   change the authoritative nameservers for the entire zone, but **do not
   do that** — see § 1 below for why you only delegate a subdomain.
3. Note the **Account ID** from the Cloudflare dashboard → right sidebar.
   You will need it for R2 bucket URLs.

---

## Section 1 — DNS / subdomain delegation

### Background

`andrewlass.com` is registered at GoDaddy, with GoDaddy as the authoritative
nameserver. GoDaddy holds all your existing records (MX, SPF, DKIM, etc.).
You do not want to move the entire zone to Cloudflare and risk disrupting
email or other existing services.

Instead, delegate only the `perfectday.andrewlass.com` sub-zone to Cloudflare.
GoDaddy remains authoritative for `andrewlass.com`; Cloudflare becomes
authoritative for `*.perfectday.andrewlass.com`. This is a standard NS
delegation — no migration of existing records required.

### Step 1.1 — Add `perfectday.andrewlass.com` as a zone in Cloudflare

Cloudflare's free plan supports full-zone management. You need to add a
zone for `perfectday.andrewlass.com` specifically (not `andrewlass.com`).

> **Note:** Cloudflare's standard "add a site" flow assumes you own the
> apex domain and want to move all NS there. For a subdomain delegation,
> you may need to contact Cloudflare support or use the "partial zone /
> CNAME setup" option under Enterprise — **or** use the simpler workaround
> below.

**Simpler workaround (no Cloudflare account zone needed):** Use Cloudflare
only for DDNS via the `andrewlass.com` zone API. Add `andrewlass.com` to Cloudflare
but do **not** change GoDaddy's nameservers. Instead, use Cloudflare in
"partial / secondary DNS" mode — or just use the Cloudflare API directly
to update records without making Cloudflare authoritative at all.

**Practical recommendation for this project:** Keep GoDaddy authoritative.
Use Cloudflare only for DDNS via its API (a scoped API token updates the A
records in GoDaddy indirectly — see § 2 for the DDNS mechanism). If you
want Cloudflare to actually be authoritative for the subdomains (for the
proxy benefit), the cleanest path is:

1. Add `perfectday.andrewlass.com` A records to GoDaddy pointing to the NUC WAN IP.
2. Create a free Cloudflare account and add `andrewlass.com` as a zone (Cloudflare
   imports all existing records from GoDaddy via DNS scan).
3. Change GoDaddy's nameservers to Cloudflare's assigned NS servers.
   Cloudflare now serves the entire `andrewlass.com` zone including all existing
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
| `diary.perfectday.andrewlass.com` | A | `<current NUC WAN IP>` | 300 | Off (grey cloud) for PoC |
| `api.diary.perfectday.andrewlass.com` | A | `<current NUC WAN IP>` | 300 | Off for PoC |
| `media.diary.perfectday.andrewlass.com` | A | `<current NUC WAN IP>` | 300 | Off for PoC |

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

The DDNS updater runs as a Docker sidecar (`timothyjmiller/cloudflare-ddns`) in
the same `docker-compose.yml` as the application stack. No manual container
management is needed — `docker compose up -d` brings it up with everything else.

### Step 2.1 — Collect the two Cloudflare values you will need

**API token:**

1. Cloudflare dashboard → **My Profile → API Tokens → Create Token**
2. Use the "Edit zone DNS" template.
3. Scope: **Zone Resources → Include → Specific zone → `andrewlass.com`**
4. Permissions: `Zone.DNS:Edit` only. Nothing else.
5. Create the token and copy it immediately — it is shown only once. Store in your password manager.

**Zone ID:**

On the Cloudflare dashboard, click your zone (`andrewlass.com` or `perfectday.andrewlass.com`) and look at the right sidebar. The Zone ID is a 32-character hex string. It is not a secret — it is visible in public DNS — but you need it for the config file.

### Step 2.2 — Provision the DDNS config file on the NUC

The `cloudflare-ddns` service is already in the in-repo `docker-compose.yml`. You do not edit the compose file. The container reads its configuration from `/etc/perfect-day/cloudflare-ddns.config.json` (mode 0600, `root:docker`), mounted at `/config.json` inside the container.

The easiest way to provision this file is to provide the API token and Zone ID when prompted by `10-secrets.sh`. The script writes the JSON for you:

```bash
cd ~/perfect-day
sudo ./scripts/nuc/10-secrets.sh
# At the "Cloudflare DDNS (optional)" prompts, enter your token and zone ID.
```

If you skipped those prompts earlier and need to provision now, re-run `10-secrets.sh`. It re-generates all auto-generated secrets if run fresh, so use this alternative to write only the DDNS config:

```bash
sudo tee /etc/perfect-day/cloudflare-ddns.config.json <<'EOF'
{
  "cloudflare": [
    {
      "authentication": { "api_token": "<your-token-here>" },
      "zone_id": "<your-zone-id-here>",
      "subdomains": [
        "diary.perfectday",
        "api.diary.perfectday",
        "media.diary.perfectday"
      ],
      "proxied": false
    }
  ],
  "a": true,
  "aaaa": false,
  "purgeUnknownRecords": false,
  "ttl": 300
}
EOF
sudo chmod 600 /etc/perfect-day/cloudflare-ddns.config.json
sudo chown root:docker /etc/perfect-day/cloudflare-ddns.config.json
```

> **Note on `subdomains`:** the container prepends the zone name automatically. Use only the left-hand portion of the FQDN here. For example, `diary.perfectday.andrewlass.com` → `"diary.perfectday"`.

> **Note on `proxied`:** leave this `false`. FortiGate handles TLS termination directly. Enabling the Cloudflare proxy would route traffic through Cloudflare's edge, which is not the intended path for this deployment.

### Step 2.3 — Start the DDNS updater

If the NUC is already deployed:

```bash
cd /opt/perfect-day
docker compose up -d cloudflare-ddns
```

If you are deploying for the first time, `20-deploy.sh` brings it up automatically as part of `docker compose up -d`. No extra step needed.

If you skipped provisioning the config file and do not want to DDNS-update records automatically, stop the container so it doesn't error-loop:

```bash
cd /opt/perfect-day
docker compose stop cloudflare-ddns
```

### Step 2.4 — Verify DDNS is working

```bash
# Container must be running:
docker compose ps cloudflare-ddns

# Check its logs for successful updates:
docker compose logs cloudflare-ddns --tail=30
# Expect: lines like "Updated A record diary.perfectday.andrewlass.com → <IP>"
# or "IP unchanged" if the WAN IP hasn't changed since last check.

# Current WAN IP from the NUC's perspective:
curl -s https://api.ipify.org

# Current DNS resolution:
dig +short diary.perfectday.andrewlass.com

# They should match. The container polls every 5 minutes by default.
```

### Failure mode

If the WAN IP changes and the updater fails to push within 15 minutes, DNS resolves to a stale address and the site is unreachable. The observability plan in `design/observability.md` includes a synthetic check for this. Set an alert on IP-mismatch lasting > 15 min.

### Alternatives (not the recommended path for this deployment)

**FortiGate built-in DDNS:** FortiOS 7.4 may have Cloudflare as a DDNS provider under **Network → DNS → Dynamic DNS**. If so, configure it with the API token from Step 2.1 — this removes any NUC component. Verify against your specific FortiOS 7.4 build; provider support varies by firmware version.

**`ddclient` on the NUC (systemd):**

```bash
sudo apt-get install -y ddclient
```

`/etc/ddclient.conf` (mode 600, root-owned):
```
daemon=300
syslog=yes
protocol=cloudflare
use=web, web=api.ipify.org
login=token
password=<your-token>
zone=andrewlass.com
diary.perfectday.andrewlass.com,api.diary.perfectday.andrewlass.com,media.diary.perfectday.andrewlass.com
```

```bash
sudo systemctl enable --now ddclient
```

If you use `ddclient`, remove the `cloudflare-ddns` service from `docker-compose.yml` or stop it to avoid duplicate updates.

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

1. Add `andrewlass.com` to Cloudflare (or just create a scoped API token if keeping GoDaddy authoritative)
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
