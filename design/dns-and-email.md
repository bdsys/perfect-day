# DNS and Email

## DNS topology

| Subdomain | Purpose |
|---|---|
| `diary.perfectday.bdsys.net` | Main web app and API |
| `media.diary.perfectday.bdsys.net` | MinIO upload endpoint — PUT-only (signed URLs), edge proxy restricts |
| `bdsys.net` | Apex domain — controls email identity (SPF/DKIM/DMARC) |

## DNS records

### A/AAAA

Both subdomains point to the NUC public IP (or cloud LB when deployed to cloud).

| Name | Type | Value | TTL |
|---|---|---|---|
| `diary.perfectday.bdsys.net` | A | `<NUC public IP>` | 300 (PoC) → 3600 (post-launch) |
| `media.diary.perfectday.bdsys.net` | A | `<NUC public IP>` | 300 (PoC) → 3600 (post-launch) |

Low TTL (300s) during PoC for easy IP changes. Raise to 3600s before public launch to reduce resolver load.

### TLS

Let's Encrypt certificates via the edge proxy:
- **NUC:** FortiGate handles ACME renewal. One cert covers `diary.perfectday.bdsys.net` + `media.diary.perfectday.bdsys.net` (SAN or wildcard).
- **Cloud:** provider-native cert manager or Cloudflare's automatic TLS.

Certificate expiry is a page-level alert in `design/observability.md`. Renewal failure must be caught before expiry, not after.

## Email deliverability (SPF, DKIM, DMARC)

All transactional email originates from SendGrid. The residential NUC IP cannot reliably send email directly — outbound port 25 is typically blocked and residential IPs are on RBLs.

### SPF

```
bdsys.net.  TXT  "v=spf1 include:sendgrid.net ~all"
```

Start with `~all` (soft-fail) for a 30-day observation window. Move to `-all` (hard-fail) only after confirming all legitimate outbound mail sources are captured. A hard-fail that blocks your own mail is worse than a soft-fail.

### DKIM

SendGrid generates DKIM keys per sending domain. The setup wizard produces two CNAME records to publish. Record the exact values once the SendGrid account is provisioned (they change per account).

```
# Placeholder — replace with actual SendGrid CNAME values at provisioning:
em1234.bdsys.net.         CNAME  em1234.bdsys.net.sendgrid.net.
s1._domainkey.bdsys.net.  CNAME  s1.domainkey.u1234567.wl012.sendgrid.net.
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
| `noreply@bdsys.net` | Send-only (no MX record needed for sending via SendGrid) |

## Sender identity

| Field | Value |
|---|---|
| From | `Perfect Day <noreply@bdsys.net>` |
| Reply-To | `support@bdsys.net` |
| Sending domain | `bdsys.net` (verified single sender in SendGrid) |

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
