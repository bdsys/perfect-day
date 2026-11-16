#!/usr/bin/env bash
# Admin CLI wrapper for Perfect Day.
# Usage: ./scripts/admin.sh <command> [options]
#
# Runs scripts/manage.py:
#   - with the local venv when it exists (dev: `make infra` or `make up`), reading
#     DATABASE_URL_SYNC from apps/api/.env;
#   - otherwise inside a one-off API container (the NUC, or a dev machine without a
#     venv), reading DATABASE_URL_SYNC from the container's environment (app.env).
# On the NUC this changes PRODUCTION data. Deletes ask you to type "yes" first.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/apps/api/.venv/bin/python"
MANAGE="$REPO_ROOT/scripts/manage.py"

if [[ -x "$VENV_PYTHON" ]]; then
    exec "$VENV_PYTHON" "$MANAGE" "$@"
fi

# The API image holds apps/api at /app but not scripts/, so mount scripts/ read-only.
# --no-deps: never start or recreate Postgres and friends for an admin command.
exec docker compose -f "$REPO_ROOT/docker-compose.yml" run --rm --no-deps \
    -v "$REPO_ROOT/scripts:/scripts:ro" -e PYTHONPATH=/app \
    api python /scripts/manage.py "$@"
