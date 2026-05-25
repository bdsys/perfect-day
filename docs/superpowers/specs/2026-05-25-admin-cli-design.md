# Admin CLI Design

**Date:** 2026-05-25
**Status:** Approved

## Context

During local development, there is no UI or admin panel yet for managing test data. Creating users, changing tiers, and seeding diaries/entries requires writing raw SQL or hitting API endpoints manually. A lightweight CLI tool removes this friction and unblocks day-to-day dev work.

The Phase 2 admin panel (#21 in `design/09-poc-scope.md`) will eventually provide a web UI for these operations. This CLI is the immediate local-dev solution, designed to stay in sync with the ORM models so it never drifts from the real schema.

## Approach

**Two files:**

- `scripts/admin.sh` — thin shell wrapper. Detects local venv (`apps/api/.venv`) and delegates to `manage.py` via it. Falls back to `docker compose exec api` when only the full Docker stack is running. The caller never needs to know which environment is active.
- `scripts/manage.py` — Python CLI using Click. Imports SQLAlchemy models directly from `apps/api/app/models/` and `apps/api/app/core/security.py`. Reads `DATABASE_URL_SYNC` from `apps/api/.env` for a synchronous psycopg2 session (same URL alembic uses — no async complexity in a CLI).

**Why synchronous session:** The existing DB layer (`app/core/database.py`) is async (asyncpg). That's appropriate for the API server but adds unnecessary complexity to a CLI. `database_url_sync` (postgresql://) is already in every `.env` for alembic — reusing it avoids introducing a second connection string.

## Commands

```
users
  list                          # table: id, email, tier, admin, deleted
  create  --email --password --tier [free] [--admin]
  delete  --email               # soft delete, requires "yes" confirmation
  set-tier --email --tier       # free | tier1 | tier2

diaries
  list    --email               # all diaries owned by user
  create  --email --name [--timezone UTC]
  delete  --diary-id            # soft delete + cascades to entries, requires "yes"

entries
  list    --diary-id            # all entries incl. soft-deleted (marked [deleted])
  create  --diary-id --date --title --body   # creates as draft/manual
  delete  --entry-id            # soft delete, requires "yes"
```

## Delete Behaviour

All deletes are soft (sets `deleted_at`). Destructive commands print exactly what will be affected and require the user to type the literal string `yes` before proceeding. Anything else aborts with no changes.

## Environment Detection

`admin.sh` checks for `apps/api/.venv/bin/python`:
- **Present** → `make infra` dev mode, run via local venv
- **Absent** → `make up` Docker mode, run via `docker compose exec api`

## New Dependency

`click>=8.1` added to `[project.optional-dependencies] dev` in `apps/api/pyproject.toml`. Dev-only — not shipped in the production Docker image.

## Documentation

`scripts/ADMIN_CLI.md` covers all commands with examples, prerequisites, and an explanation of how the wrapper works.

## Admin Console (Phase 2)

Item #21 in `design/09-poc-scope.md` is updated to explicitly include user CRUD, tier management, diary CRUD, and entry CRUD as part of the Phase 2 web admin panel scope.
