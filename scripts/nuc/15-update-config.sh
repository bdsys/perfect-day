#!/usr/bin/env bash
# scripts/nuc/15-update-config.sh — Update an existing NUC's config in place.
#
# Usage: sudo ./scripts/nuc/15-update-config.sh [--google-client <client.json>]
#
# Sets the public hostnames in /etc/perfect-day/app.env, and, with --google-client,
# the Google OAuth client from the JSON file Google Cloud lets you download for a
# Web client (it must be the "Perfect Day NUC" client: its redirect URI is checked).
# Also rewrites /etc/perfect-day/cloudflare-ddns.config.json with the current names
# and proxied: true, keeping its API token and zone ID.
#
# Never re-run 10-secrets.sh on a running NUC instead: it regenerates MASTER_SECRET,
# and the photos and Google tokens already stored are encrypted with it.
#
# Each file it changes is copied to <file>.bak-<timestamp> first (same mode).
# Secrets are never printed.
set -euo pipefail

WEB_HOST="perfectday.andrewlass.com"
API_HOST="perfectday-api.andrewlass.com"
DDNS_SUBDOMAINS='["perfectday", "perfectday-api", "perfectday-media"]'

APP_ENV="${PD_APP_ENV:-/etc/perfect-day/app.env}"
DDNS_CONFIG="${PD_DDNS_CONFIG:-/etc/perfect-day/cloudflare-ddns.config.json}"

if [ "$(id -u)" -ne 0 ] && [ "${PD_ALLOW_NONROOT:-}" != "1" ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

GOOGLE_CLIENT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --google-client)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "Usage: $0 [--google-client <client.json>]" >&2; exit 2
            fi
            GOOGLE_CLIENT="$2"; shift 2 ;;
        *) echo "Usage: $0 [--google-client <client.json>]" >&2; exit 2 ;;
    esac
done

[ -f "${APP_ENV}" ] || { echo "ERROR: ${APP_ENV} not found." >&2; exit 1; }
if [ -n "${GOOGLE_CLIENT}" ] && [ ! -f "${GOOGLE_CLIENT}" ]; then
    echo "ERROR: ${GOOGLE_CLIENT} not found." >&2; exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

WEB_HOST="${WEB_HOST}" API_HOST="${API_HOST}" DDNS_SUBDOMAINS="${DDNS_SUBDOMAINS}" \
STAMP="${STAMP}" python3 -I - "${APP_ENV}" "${DDNS_CONFIG}" "${GOOGLE_CLIENT}" <<'PY'
import json, os, re, shutil, sys

app_env, ddns_config, google_client = sys.argv[1:4]
web, api = os.environ["WEB_HOST"], os.environ["API_HOST"]
stamp = os.environ["STAMP"]
redirect = f"https://{api}/v1/integrations/google/callback"

values = {
    "CORS_ORIGINS": json.dumps([f"https://{web}"]),
    "GOOGLE_REDIRECT_URI": redirect,
    "NEXT_PUBLIC_API_URL": f"https://{api}",
}

if google_client:
    try:
        client = json.load(open(google_client))["web"]
    except (ValueError, KeyError) as exc:
        sys.exit(f"ERROR: {google_client} is not a Google Web client file ({exc}).")
    if redirect not in client.get("redirect_uris", []):
        sys.exit(f"ERROR: {google_client} does not list {redirect} as a redirect URI. "
                 "Use the 'Perfect Day NUC' client. Nothing changed.")
    if f"https://{web}" not in client.get("javascript_origins", []):
        sys.exit(f"ERROR: {google_client} does not list https://{web} as a JavaScript origin. "
                 "Nothing changed.")
    values["GOOGLE_CLIENT_ID"] = client["client_id"]
    values["GOOGLE_CLIENT_SECRET"] = client["client_secret"]
    values["NEXT_PUBLIC_GOOGLE_CLIENT_ID"] = client["client_id"]


def backup(path):
    dest = f"{path}.bak-{stamp}"
    shutil.copy2(path, dest)  # keeps mode; owner is root either way
    return dest


def write_like(path, text):
    """Replace the file's content, keeping its mode and owner."""
    st = os.stat(path)
    tmp = f"{path}.tmp-{stamp}"
    # Created 0600 so the secrets are never readable by others, even for a moment.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
    except BaseException:
        os.unlink(tmp)
        raise
    os.chmod(tmp, st.st_mode & 0o777)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    os.replace(tmp, path)


# app.env
lines = open(app_env).read().splitlines()
seen = set()
for i, line in enumerate(lines):
    key = line.split("=", 1)[0].strip()
    if key in values and not line.lstrip().startswith("#"):
        lines[i] = f"{key}={values[key]}"
        seen.add(key)
lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
saved = backup(app_env)
write_like(app_env, "\n".join(lines) + "\n")
print(f"  {app_env}: set {', '.join(values)}")
print(f"  previous version kept at {saved}")

# DDNS config. The NUC's copy was written by an old heredoc and can hold a stray
# newline inside the token, which made the updater refuse to start; read the two
# values leniently and write clean JSON.
if os.path.exists(ddns_config):
    raw = open(ddns_config).read()
    def grab(name):
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % name, raw)
        return re.sub(r"[\x00-\x1f\s]", "", m.group(1)) if m else ""
    token, zone = grab("api_token"), grab("zone_id")
    if not token or not zone:
        # Exit non-zero so nobody restarts an updater that cannot work.
        sys.exit(f"ERROR: {ddns_config}: no API token or zone ID found; left unchanged. "
                 "app.env WAS updated. Fix the DDNS file by hand (deploy/cloudflare.md § 2.2).")
    else:
        config = {
            "cloudflare": [{
                "authentication": {"api_token": token},
                "zone_id": zone,
                "subdomains": json.loads(os.environ["DDNS_SUBDOMAINS"]),
                "proxied": True,
            }],
            "a": True,
            "aaaa": False,
            "purgeUnknownRecords": False,
            "ttl": 300,
        }
        saved = backup(ddns_config)
        write_like(ddns_config, json.dumps(config, indent=2) + "\n")
        print(f"  {ddns_config}: names {', '.join(config['cloudflare'][0]['subdomains'])}, proxied: true")
        print(f"  previous version kept at {saved}")
else:
    print(f"  {ddns_config}: not present; DDNS not configured")
PY

echo "Done. Apply with: sudo ./scripts/nuc/40-update.sh"
