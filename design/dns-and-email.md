# DNS and Email

## DNS topology

| Subdomain | Purpose |
|---|---|
| `perfectday.andrewlass.com` | Web app |
| `perfectday-api.andrewlass.com` | API |
| `perfectday-media.andrewlass.com` | Unused: photos go through the API (S-7); kept for a possible media host |

Every public name has exactly one label before `andrewlass.com`: Cloudflare's free Universal SSL covers only the apex and first-level subdomains (P-9). Current setup: [`deploy/cloudflare.md`](../deploy/cloudflare.md).
| `andrewlass.com` | Apex domain — controls email identity (SPF/DKIM/DMARC) |

## DNS records

### A/AAAA

All three names point to the NUC public IP (or cloud LB when deployed to cloud), proxied through Cloudflare.

| Name | Type | Value | Proxy | TTL |
|---|---|---|---|---|
| `perfectday.andrewlass.com` | A | `<NUC public IP>` | Proxied | Auto |
| `perfectday-api.andrewlass.com` | A | `<NUC public IP>` | Proxied | Auto |
| `perfectday-media.andrewlass.com` | A | `<NUC public IP>` | Proxied | Auto |

Proxied records always use TTL Auto (300 s); Cloudflare does not allow changing it (<https://developers.cloudflare.com/dns/proxy-status/>).

### TLS

Two separate TLS hops are used in the NUC deployment:

- **Browser ↔ Cloudflare:** Cloudflare Universal SSL (browser-trusted, auto-renewed by CF).
- **Cloudflare ↔ FortiGate:** Cloudflare Origin Certificate for `*.andrewlass.com, andrewlass.com`, signed by Cloudflare's private CA, **expires 2027-11-16** (Cloudflare sends no expiry notice). Installed on FortiGate; private key generated on FortiGate via CSR, never exported. See [`deploy/cloudflare.md` § Section 4](../deploy/cloudflare.md#section-4--origin-certificate-nuc-deployment).
- **Cloud:** provider-native cert manager or Cloudflare's automatic TLS.

Certificate expiry is a page-level alert in `design/observability.md`. Renewal failure must be caught before expiry, not after.

## Email deliverability (SPF, DKIM, DMARC)

All transactional email originates from SendGrid. The residential NUC IP cannot reliably send email directly — outbound port 25 is typically blocked and residential IPs are on RBLs.

### SPF

```
andrewlass.com.  TXT  "v=spf1 include:sendgrid.net ~all"
```

Start with `~all` (soft-fail) for a 30-day observation window. Move to `-all` (hard-fail) only after confirming all legitimate outbound mail sources are captured. A hard-fail that blocks your own mail is worse than a soft-fail.

### DKIM

SendGrid generates DKIM keys per sending domain. The setup wizard produces two CNAME records to publish. Record the exact values once the SendGrid account is provisioned (they change per account).

```
# Placeholder — replace with actual SendGrid CNAME values at provisioning:
em1234.andrewlass.com.         CNAME  em1234.andrewlass.com.sendgrid.net.
s1._domainkey.andrewlass.com.  CNAME  s1.domainkey.u1234567.wl012.sendgrid.net.
```

### DMARC

Roll out in phases:

| Phase | Record | When |
|---|---|---|
| 1 — Observe | `v=DMARC1; p=none; rua=mailto:dmarc-reports@bdsys.net` | Days 0–30 |
| 2 — Quarantine | `v=DMARC1; p=quarantine; pct=100; rua=mailto:dmarc-reports@bdsys.net` | After SPF+DKIM alignment confirmed |
| 3 — Reject | `v=DMARC1; p=reject; pct=100; rua=mailto:dmarc-reports@bdsys.net` | Production target |

`dmarc-reports@bdsys.net` routes to the operator's inbox via existing MX. Review the weekly aggregate reports before moving to `p=quarantine`.

### Reverse DNS / PTR

Not required. SendGrid sends on their own IPs; PTR records are on their infrastructure.

## Outbound mail provider

**Primary:** SendGrid.

**Free-tier limit (verify at provisioning):** approximately 100 emails/day on the free tier — check current terms at sign-up. For a personal/family diary with a handful of users, 100/day is sufficient during PoC. If the limit becomes a problem:
- **Fallback:** Postmark (free tier: 100 emails/mo on developer plan; higher limits on paid). Postmark has better deliverability reputation than SendGrid on some RBLs.
- **Alternative:** AWS SES ($0.10/1000 emails, no free tier limit after the first 62k/mo from EC2). Cheapest at scale but adds AWS dependency.

SendGrid API key is in `design/secrets.md` inventory.

## Inbound mail

Out of scope for PoC. Inbound routing is not configured.

| Address | Routes to |
|---|---|
| `support@bdsys.net` | Operator's personal inbox (existing MX) |
| `dmarc-reports@bdsys.net` | Operator's personal inbox (existing MX) |
| `pd@bdsys.net` | Send-only (no MX record needed for sending via SendGrid) |

## Sender identity

| Field | Value |
|---|---|
| From | `Perfect Day <pd@bdsys.net>` |
| Reply-To | `support@bdsys.net` |
| Sending domain | `andrewlass.com` (verified single sender in SendGrid) |

## Email templates

All transactional emails use SendGrid Dynamic Templates. Template IDs are stored in app config (not secrets — they're non-sensitive identifiers).

| Template | Trigger | Content |
|---|---|---|
| Magic link | `POST /v1/auth/magic-link/request` | One-time sign-in link (15-min TTL) |
| Password reset | `POST /v1/auth/password/forgot` | Reset token link |
| Account deletion grace | Account marked for deletion | "Your account will be deleted in 7 days" + restore link |
| Account deletion restore window closing | Day 6 of 7-day grace | "Last chance to restore" |
| Diary deletion grace | Diary marked for deletion | "Your diary will be deleted in 30 days" + restore link |
| Diary deletion restore window closing | Day 28 of 30-day grace | "Last chance to restore" |
| Shared diary invite | `POST /v1/diaries/{id}/invitations` | Invite link |
| Admin impersonation notice | `POST /v1/admin/users/{id}/impersonate` | "An admin viewed your account" with timestamp (see M20) |
| Email address changed | `PATCH /v1/auth/me` (email change) | Sent to old address with revert link (see M21) |

## Deliverability monitoring

- **During PoC:** weekly manual review of the SendGrid dashboard (bounces, spam reports, delivery rate).
- **Post-launch:** alert in `design/observability.md` on bounce rate > 5% and on spam complaint rate > 0.1%.
- **DMARC aggregate reports:** review weekly during the `p=none` observation window.

## Dynamic DNS (Comcast residential IP)

The NUC sits behind a Comcast residential connection with a dynamic public
IPv4. Comcast does not guarantee a static lease — the address can change
on modem reboot, lease renewal, or upstream maintenance. The DNS A records
above (`perfectday.andrewlass.com`, `perfectday-media.andrewlass.com`)
must track the current WAN IP automatically, otherwise the service goes
dark whenever the lease rolls.

### Recommended approach: Cloudflare DDNS via API

Use Cloudflare as the authoritative DNS for `andrewlass.com` (already the plan
per the TLS section above) and run a small updater on the NUC that pushes
the current WAN IP to Cloudflare's DNS API on a schedule.

**Why Cloudflare over a dedicated DDNS service:**
- DNS is already at Cloudflare, so no second vendor to operate.
- We get to keep the real domain (`perfectday.andrewlass.com`) instead of
  a third-party hostname like `perfectday.duckdns.org`.
- Free plan covers unlimited A-record updates.
- API token can be scoped to "Edit DNS for zone andrewlass.com" only — much
  smaller blast radius than a global API key.
- No periodic confirmation emails / hostname-expiry games.

**Mechanism:**
1. Create a Cloudflare API token scoped to `Zone.DNS:Edit` for `andrewlass.com`.
   Store on the NUC at `/etc/perfect-day/cloudflare-ddns.config.json`, mode 0600.
2. Run [`ddclient`](https://ddclient.net/) (Perl, in Debian/Ubuntu repos)
   or [`cloudflare-ddns`](https://github.com/timothymiller/cloudflare-ddns)
   (Python, Docker-friendly) as a systemd service or container on the NUC.
3. Updater polls a "what's my IP" endpoint (e.g., `https://api.ipify.org`)
   every 5 minutes, compares against the current Cloudflare record, and
   `PATCH`es the A record only on change.
4. TTL stays at 300s (already the documented PoC value), so propagation
   after a Comcast IP roll is bounded by ~5 min poll + 5 min TTL = ~10 min
   worst case.

**Reference (Cloudflare official):** [Managing dynamic IP addresses](https://developers.cloudflare.com/dns/manage-dns-records/how-to/managing-dynamic-ip-addresses/)

### Alternatives considered

| Service | Verdict |
|---|---|
| **DuckDNS** ([duckdns.org](https://www.duckdns.org/)) | Free, AWS-hosted, simple token-based update URL. Good fallback, but only gives a `*.duckdns.org` hostname — would force a CNAME hop and a second cert SAN. Use only if Cloudflare DDNS goes down. |
| **No-IP free** ([noip.com/free](https://www.noip.com/free)) | Free tier requires manual confirmation **every 30 days** by clicking an email link, or the hostname expires. Operationally hostile for a passive home server. Skip. |
| **Dynu, FreeDNS afraid.org** | Workable but adds a vendor for no win over Cloudflare. Skip. |
| **FortiGate built-in DDNS** | The FortiGate edge supports DDNS to several providers natively, including Cloudflare via custom API endpoints. Worth using if available — removes the need for a script on the NUC. Verify exact provider list against the deployed FortiOS 7.2+ config; if Cloudflare is supported, prefer this and drop `ddclient`. |

### Failure mode and alert

If the WAN IP changes and the updater fails to push within 15 minutes,
DNS resolves to a stale address and the site is down. Add a synthetic
check (covered in `design/observability.md`) that resolves
`perfectday.andrewlass.com` and compares to the current public IP
(`api.ipify.org`); page on mismatch lasting > 15 min.

### Verification

> **Note:** With Cloudflare proxy **on** (orange cloud), `dig +short perfectday.andrewlass.com`
> returns Cloudflare anycast IPs, not your home WAN IP. This is expected. To verify the DDNS
> updater pushed the correct origin IP, check the A record value directly in the Cloudflare dashboard,
> or compare `docker compose logs cloudflare-ddns` output against `curl https://api.ipify.org`.

1. Note the current A record origin IP in the Cloudflare dashboard (DNS tab) for `perfectday`.
2. Reboot the Comcast modem (forces a DHCP renew; the lease often, but
   not always, returns a different IP).
3. Within 10 minutes, the Cloudflare dashboard A record should match `curl https://api.ipify.org`
   from the NUC. `dig +short perfectday.andrewlass.com` will return CF anycast IPs — that is correct.
4. `curl -s https://perfectday-api.andrewlass.com/readyz` returns 200 from
   off-network (mobile hotspot).
