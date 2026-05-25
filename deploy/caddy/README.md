# Caddy Edge Proxy

Caddy runs as a Docker service on the NUC, listening on port 80. It does Host-header-based routing between the two application subdomains:

| Host header | Backend |
|---|---|
| `diary.perfectday.andrewlass.com` | `web:3000` (Next.js) |
| `api.diary.perfectday.andrewlass.com` | `api:8000` (FastAPI) |

Any other `Host` value returns 404.

**Why Caddy instead of FortiGate routing:** FortiGate's `firewall vip` does not support HTTP Host-header routing in any FortiOS version — that feature belongs to FortiADC, a separate product. Caddy handles the L7 dispatch; FortiGate handles TLS termination and WAF/IPS on the decrypted plaintext.

**TLS:** FortiGate terminates the Cloudflare↔origin TLS hop (Cloudflare Origin Certificate). Caddy receives plain HTTP on port 80 — `auto_https off` is set intentionally.

---

## Before deploying to the NUC

Edit `Caddyfile` and replace `<FORTIGATE_LAN_IP>` with the actual LAN IP of your FortiGate (the IP it uses on the home LAN, e.g. `192.168.1.1`). This IP is used to scope which upstream proxy is trusted to set `X-Forwarded-For` / `X-Forwarded-Proto` headers.

---

## Local debug workflow

Use this when you need to test the production-shaped Host-routing path locally — e.g., debugging an `X-Forwarded-Proto` issue, validating a CORS header that fires only on a real hostname, or rehearsing a deploy.

### Step 1 — Add hosts entries (one-time setup)

```bash
sudo tee -a /etc/hosts <<'EOF'
127.0.0.1  diary.perfectday.local api.diary.perfectday.local
EOF
```

### Step 2 — Edit `deploy/caddy/Caddyfile` to use local hostnames (don't commit)

Change the two `host` matchers in `deploy/caddy/Caddyfile`:
```
@diary host diary.perfectday.local
@api host api.diary.perfectday.local
```

### Step 3 — Start the stack with the `nuc` profile

```bash
docker compose --profile nuc up -d
```

This starts everything in `make up` PLUS the Caddy edge. Do NOT include `-f docker-compose.nuc.yml` — that file no longer exists. Do NOT include `-f docker-compose.dev.yml` separately if you want Caddy to be the only entry point; or run both together to have both Caddy and direct access.

### Step 4 — Test

```bash
curl -sH "Host: diary.perfectday.local" http://localhost:80/
# Expect: Next.js HTML response

curl -sH "Host: api.diary.perfectday.local" http://localhost:80/healthz
# Expect: {"status":"ok"} from FastAPI
```

Or browse to `http://diary.perfectday.local` and `http://api.diary.perfectday.local` (after adding `/etc/hosts` entries).

### Step 5 — Tear down

```bash
docker compose --profile nuc down
git checkout -- deploy/caddy/Caddyfile   # revert the hostname edits
```

**Why no TLS in local debug:** `auto_https off` means Caddy serves plain HTTP. In production, TLS is terminated by FortiGate before traffic reaches Caddy. Locally, there is no TLS at all — just like the FortiGate→NUC LAN hop.

---

## Future: end-to-end TLS to NUC

The FortiGate→NUC hop is currently plain HTTP (trusted LAN segment). If full end-to-end TLS is needed later, the change is minimal:

1. Change `:80` to `:8443` in `Caddyfile` and add `tls internal` inside the server block
2. Update the FortiGate VIP realserver port to `8443` and set SSL mode to `full`
3. Port `8443` is already reserved for this purpose in `PORTS.md`
