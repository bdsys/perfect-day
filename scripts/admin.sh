#!/usr/bin/env bash
# Admin CLI wrapper for Perfect Day.
# Usage: ./scripts/admin.sh <command> [options]
# Runs manage.py via local venv (make infra mode) or docker compose exec (make up mode).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/apps/api/.venv/bin/python"
MANAGE="$REPO_ROOT/scripts/manage.py"

if [[ -x "$VENV_PYTHON" ]]; then
    # Local venv exists — use it directly (works with `make infra`)
    exec "$VENV_PYTHON" "$MANAGE" "$@"
else
    # Fall back to running inside the api container (works with `make up`)
    exec docker compose -f "$REPO_ROOT/docker-compose.yml" exec api python /workspace/scripts/manage.py "$@"
fi
