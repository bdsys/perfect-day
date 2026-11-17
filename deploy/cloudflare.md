# Cloudflare Setup

How Perfect Day uses Cloudflare, as configured and verified on 2026-11-16 (PoC 25.6, finding P-9
in [`design/code-review-2026-11.md`](../design/code-review-2026-11.md)). The FortiGate side is in
[`deploy/nuc.md` § FortiGate Virtual Server setup](nuc.md#fortigate-virtual-server-setup).

---

## What Cloudflare is used for

| Use | Deployment target | Cost |
|---|---|---|
| Authoritative DNS for the whole `andrewlass.com` zone | All | Free |
| DDNS: keeps the A records on the Comcast dynamic WAN IP | NUC | Free |
| **Proxy (orange cloud):** public TLS via Universal SSL; hides the home WAN IP | NUC | Free |
| Origin Certificate for the Cloudflare→FortiGate TLS hop | NUC | Free |
| R2 object storage for encrypted photo chunks | Hybrid (NUC + CX21) | Free at PoC scale |

---

## Section 1 — Zone and hostnames

The whole `andrewlass.com` zone is on Cloudflare (nameservers `*.ns.cloudflare.com`); GoDaddy is
only the registrar. Other sites share the zone (`andrewlass.com` and `www` are served elsewhere),
so zone-wide settings affect them too.

**Every public name has exactly one label before `andrewlass.com`.** Cloudflare's free Universal
SSL covers "the root domain … and first-level subdomains" only; deeper names "will not serve a
valid certificate"
([Cloudflare](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/)).
The original `diary.perfectday.andrewlass.com` names were two levels deep and never worked.

### DNS records

| Name | Type | Content | Proxy | TTL | Used for |
|---|---|---|---|---|---|
| `perfectday` | A | home WAN IP | **Proxied** | Auto | Web (Next.js) |
| `perfectday-api` | A | home WAN IP | **Proxied** | Auto | API (FastAPI) |
| `perfectday-media` | A | home WAN IP | **Proxied** | Auto | Reserved for photo links; no route yet (P-10) |

- Proxied records always use TTL Auto (300 s); it cannot be changed
  ([Cloudflare](https://developers.cloudflare.com/dns/proxy-status/)).
- With the proxy on, `dig` returns Cloudflare's IPs (104.21.x.x, 172.67.x.x), never the WAN IP.
- The old `diary.perfectday`, `api.diary.perfectday` and `media.diary.perfectday` records are to be
  deleted once the app is deployed on the new names.

### SSL/TLS settings

- **Encryption mode: Full (strict)** (SSL/TLS → Overview). It is zone-wide. Do not change it: other
  sites share the zone. To change it for our names only, use a Configuration Rule
  ([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/)).
- **Edge certificate:** the Universal SSL certificate for `andrewlass.com, *.andrewlass.com`
  (Google Trust Services) covers all three names. Nothing to configure.
- **Origin certificate:** `*.andrewlass.com, andrewlass.com`, installed on the FortiGate.
  **Expires 2027-11-16.** See Section 4.

---

## Section 2 — DDNS (dynamic Comcast IP)

The NUC has a Comcast residential connection with a dynamic public IPv4. The A records above must
follow it. The updater is the `cloudflare-ddns` service in `docker-compose.yml`
(`timothyjmiller/cloudflare-ddns`, `nuc` profile). Background and alternatives:
[`design/dns-and-email.md`](../design/dns-and-email.md) § Dynamic DNS.

### Step 2.1 — API token and zone ID

- **API token:** My Profile → API Tokens → Create Token → "Edit zone DNS" template. Zone Resources:
  Include → Specific zone → `andrewlass.com`. Permission `Zone.DNS:Edit` only. It is shown once;
  store it in your password manager. A token scoped to the zone covers every record in it.
  New tokens start with `cfut_` ([token formats](https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/)).
  **Not the Global API Key** (it starts with `cfk_`, and is under "API Keys" on the same page):
  the updater rejects it with error 9109 "Invalid access token", and it can do anything on the
  account. `15-update-config.sh` refuses it.
- **Zone ID:** the zone's Overview page, right sidebar. Not a secret.

### Step 2.2 — The config file on the NUC

`sudo ./scripts/nuc/10-secrets.sh` writes `/etc/perfect-day/cloudflare-ddns.config.json`
(mode 0600) when you give it the token and zone ID. It contains:

```json
{
  "cloudflare": [
    {
      "authentication": { "api_token": "<token>" },
      "zone_id": "<zone id>",
      "subdomains": ["perfectday", "perfectday-api", "perfectday-media"],
      "proxied": true
    }
  ],
  "a": true,
  "aaaa": false,
  "purgeUnknownRecords": false,
  "ttl": 300
}
```

- **`subdomains`:** the container adds the zone name, so `perfectday` means
  `perfectday.andrewlass.com`.
- **`proxied` must be `true`.** Until 2026-11-16 the script wrote `false`. If the updater applies
  that, it switches the records to DNS only: browsers then connect straight to the FortiGate, get the
  Origin certificate (not publicly trusted) and fail, and the home WAN IP is public.

To set the token and zone ID on a running NUC without regenerating the other secrets, run
`sudo ./scripts/nuc/15-update-config.sh --ddns-credentials` (it asks for both, hides the token,
and checks their format), then `docker compose --profile nuc up -d --force-recreate cloudflare-ddns`.
Never re-run `10-secrets.sh` on a NUC with data: it regenerates `MASTER_SECRET`.

### Step 2.3 — Verify

```bash
docker compose --profile nuc ps cloudflare-ddns
docker compose --profile nuc logs cloudflare-ddns --tail=30
# Expect: updates or "unchanged" for perfectday, perfectday-api, perfectday-media
```

`dig` shows Cloudflare's IPs, not the WAN IP. To check the origin IP, compare the record's
Content in the Cloudflare dashboard with `curl https://api.ipify.org` run on the NUC, and check the
records still show the orange cloud.

### Failure mode

If the WAN IP changes and the updater fails, Cloudflare forwards to a stale address and the site
is unreachable (error 521/522). See `design/observability.md` for the IP-mismatch check.

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

---

## Section 4 — Origin certificate (NUC deployment)

The Cloudflare→FortiGate hop uses a Cloudflare Origin CA certificate, which Full (strict)
accepts
([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)).
Browsers do not trust it; only Cloudflare's edge does.

- **Names:** `*.andrewlass.com, andrewlass.com`. A wildcard covers one level only
  ([Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)), which is
  exactly our naming rule.
- **Made by:** a CSR generated on the FortiGate (the key never leaves it), signed under SSL/TLS →
  Origin Server → Create Certificate. Step-by-step:
  [`deploy/nuc.md` § Step 1](nuc.md#step-1--origin-certificate).
- **Expires 2027-11-16** (issued for one year). Cloudflare sends no expiry notice for Origin CA
  certificates. An expired certificate gives Cloudflare error 526 on every request.

---

## Setup sequence summary (NUC)

1. DNS: the three proxied A records in Section 1.
2. SSL/TLS mode: confirm Full (strict).
3. DDNS: token and zone ID into `10-secrets.sh`; confirm `proxied: true` (Section 2).
4. Origin certificate on the FortiGate (Section 4 and `deploy/nuc.md`).
5. FortiGate VIP, health check and proxy-mode policy (`deploy/nuc.md`).
6. Verify: `deploy/nuc.md` § Step 5.

The hybrid deployment adds R2 (Section 3).

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
