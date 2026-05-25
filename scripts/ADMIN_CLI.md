# Admin CLI

Local-only developer tool for managing Perfect Day data directly against the database.
**Do not use against production.**

## Requirements

Either:
- `make infra` running (postgres on localhost:5432) with a local venv (`make bootstrap`)
- Or `make up` running (full Docker stack)

The wrapper script (`admin.sh`) detects which is available automatically.

## Usage

```bash
./scripts/admin.sh <group> <command> [options]
```

Run `./scripts/admin.sh --help` or `./scripts/admin.sh <group> --help` for full option listings.

## Soft vs hard delete

All `delete` commands are **soft delete by default** — they set `deleted_at` on the row(s) but the data stays in the DB.

Pass `--hard` for **permanent deletion** (DB foreign-key cascades remove related rows). Both modes show what will be affected and require typing `yes`.

To completely reset your local DB instead, run `make down -v` followed by `make infra && make migrate`.

---

## Users

### List all users
```bash
./scripts/admin.sh users list
```

### Create a user
```bash
./scripts/admin.sh users create --email alice@example.com --password secret123 --tier free
./scripts/admin.sh users create --email admin@example.com --password secret123 --tier tier2 --admin
```

Tiers: `free` | `tier1` | `tier2`

### Change a user's tier
```bash
./scripts/admin.sh users set-tier --email alice@example.com --tier tier1
```

### Delete a user
```bash
# Soft delete (cascades deleted_at to all their diaries and entries)
./scripts/admin.sh users delete --email alice@example.com

# Hard delete (DB FK cascades remove diaries, entries, oauth tokens, photos, etc.)
./scripts/admin.sh users delete --email alice@example.com --hard
```

---

## Diaries

### List diaries for a user
```bash
./scripts/admin.sh diaries list --email alice@example.com
```

### Create a diary
```bash
./scripts/admin.sh diaries create --email alice@example.com --name "Baby's First Year"
./scripts/admin.sh diaries create --email alice@example.com --name "Family Journal" --timezone "America/Los_Angeles"
```

Default timezone: `UTC`. A `scan_job` row is created automatically (matches API behavior).

### Delete a diary
```bash
# Soft delete (also cascades deleted_at to all entries in the diary)
./scripts/admin.sh diaries delete --diary-id <uuid>

# Hard delete
./scripts/admin.sh diaries delete --diary-id <uuid> --hard
```

---

## Entries

### List entries in a diary
```bash
./scripts/admin.sh entries list --diary-id <uuid>
```

Shows all entries including soft-deleted ones (marked `[deleted]`).

### Create a draft entry
```bash
./scripts/admin.sh entries create --diary-id <uuid> --date 2026-01-15 --title "First steps" --body "She took her first steps today."
```

`--title` and `--body` are optional. Date must be `YYYY-MM-DD` format. Always created with `status=draft`, `body_source=fallback`.

### Delete an entry
```bash
./scripts/admin.sh entries delete --entry-id <uuid>
./scripts/admin.sh entries delete --entry-id <uuid> --hard
```

---

## How it works

`admin.sh` checks for `apps/api/.venv/bin/python`:
- **Found** → runs `scripts/manage.py` via the local venv (used with `make infra`)
- **Not found** → runs `docker compose exec api python /workspace/scripts/manage.py` (used with `make up`)

`manage.py` reads `DATABASE_URL_SYNC` from `apps/api/.env` and opens a synchronous SQLAlchemy session. It imports models from `apps/api/app/models/` directly so the schema is always in sync with the ORM.
