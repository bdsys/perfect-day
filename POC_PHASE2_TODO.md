# POC Phase 2 — Master Todo

Status tracker for Phase 2. Full feature specs live in [`design/09-poc-scope.md`](design/09-poc-scope.md) — this file tracks only what's done and what's left.

---

## Build order

Items must be built in wave order. Within a wave, items are independent and can run in parallel.

### Re-entry — do before resuming feature work
| # | Feature | Status |
|---|---|---|
| 25 | Full code review of everything shipped so far (Phase 1 + Phase 2 items 13, 15, 16, 17, 19, 22, 23) | **review done; fixes on `chore/code-review-2026-11`, `make test-all` green 2026-11-11, not yet committed or merged** |

Added 2026-11-11 after a five-month gap. Findings, what was fixed and what is still open are in [`design/code-review-2026-11.md`](design/code-review-2026-11.md). Before starting items 24, 14 and 18: commit and merge the fix branch.

The six open questions were answered on 2026-11-11. The decided designs and their build order are in [`docs/superpowers/specs/2026-11-11-code-review-decisions-design.md`](docs/superpowers/specs/2026-11-11-code-review-decisions-design.md); they are approved in conversation and not yet built:

| # | Follow-up | Status |
|---|---|---|
| 25.1 | Scan and rules: entries only for ended events, cancellations and reschedules applied, one entry per event | **done** — PR #41, merged 2026-11-12 |
| 25.2 | Drafting model: `claude-opus-5-5` as a setting, call-site fixes, weekly model check | **done** — PR #42, merged 2026-11-12 |
| 25.3 | AI suggestion box (Regenerate never overwrites existing text) | pending |
| 25.4 | Self-updating NUC: CI builds images, NUC pulls on a timer, SSH deploy removed | pending |
| 25.5 | Delete test photos, remove old-format photo reading code | pending |
| 26 | Automatic entries with no rule-writing — **needs its own design first** | backlog |

### Wave A — independent, no prerequisites
| # | Feature | Status |
|---|---|---|
| 22 | Gemini fallback for LLM | **done** |
| 17 | Backfill — slice 1 (Calendar-only, spec-compliant) | **done** |
| 15 | Multi-day entry support | **done** |

Build 22 first within Wave A — it introduces the LLM provider abstraction that 15 and 16 build on.

### Wave B — requires Wave A complete
| # | Feature | Status |
|---|---|---|
| 13 | MinIO + photo upload | **done** |
| 16 | Weather enrichment (Open-Meteo) | **done** |
| 19 | Diary create wizard + edit settings | **done** |
| 23 | Manual entry creation form (popover) | **done** |
| 24 | Per-entry location & time (lat/lon + tz_override) | pending |

16's backfill extension requires 17 done first.

### Wave C — requires Wave B complete
| # | Feature | Status |
|---|---|---|
| 14 | Google Photos grant + scan | pending |
| 18 | Tier enforcement | pending |

Build 14 before 18. 18 must gate photo uploads (needs 13) and worker auto-entries (touches `tasks.py` after 14/15/17 settle).

---

## Hard dependencies

- 14 requires 13 (Photos needs MinIO storage)
- 14 extends 17 (adds `google_photos` source to backfill — wire as part of item 14, not 17)
- 16 extends 17 (adds weather to backfill chunks — wire as part of item 16, not 17)
- 18 photo-tier check requires 13 (can't gate uploads that don't exist yet)

---

## Per-item scaffold status

What's already in the codebase vs. what needs to be built. Verified by direct code read during Phase 2 planning audit (2026-05-27).

| # | Feature | Already exists | Still missing |
|---|---|---|---|
| 22 | Gemini fallback | Anthropic call site in `workers/llm.py` | LLM provider abstraction layer, Gemini SDK, fallback decision logic |
| 17 | Backfill slice 1 | `POST /scan/backfill` endpoint, `BackfillRun` model, `run_backfill()` worker, 365-day cap | 4 spec gaps: body is `{days}` not `{from_date, to_date, sources}`; no weekly chunking/2s sleep; no `scan_lock:{diary_id}`; no `DELETE /scan/backfill/{runId}` |
| 15 | Multi-day entries | `entry_end_date` column, API/CRUD accepts it, TZ helper | Worker grouping logic for events spanning midnight; timeline UI for multi-day entries |
| 13 | MinIO + photo upload | AES-GCM helpers (`core/security.py:87-101`), `Photo`/`EntryPhoto`/`DiaryPhoto` models | S3 client wiring, `upload-url`/`finalize` endpoints, per-photo DEK encryption, decrypt-and-stream download |
| 16 | Weather enrichment | `open_meteo.py` client, `enrichments.py` orchestrator, migration 0009, Diary lat/lon, citation validator extended | **done** — all code shipped; `make test-all` green |
| 14 | Google Photos | OAuth scaffolding reusable from Calendar (same `oauth_tokens` table, encryption, partial-grant pattern) | Photos scope + grant flow, `ingest_photo` worker, metadata-first filter, `entry_photos` attachment, backfill extension |
| 18 | Tier enforcement | `services/tier.py` with `enforce_entry_tier_limit`; wired into manual-entry router paths | Not called from worker auto-entry path (`workers/tasks.py`); not gating photo uploads; 403 + structured error in UI |
| 19 | Diary create wizard + edit settings | `PATCH /v1/diaries/{id}` endpoint + `DiaryPatch` schema; `subject_name`/`subject_relation` already in `DiaryOut` | **done** — merged in PR #38 (2026-06-10). `/diaries/new` and `/diaries/[diaryId]/settings` pages, `api.diaries.patch()`, `voice_override`/`tone_hint` on `DiaryCreate`/`DiaryOut`, Playwright spec `diary-create-settings.spec.ts`. |
| 23 | Manual entry form | `"New entry"` button + `handleNewEntry` (hardcodes today); `.popover` CSS class; backend `EntryCreate` accepts `entry_end_date` | **done** — Widen `entries.create` type to include `entry_end_date`; popover form with date/end_date/title inputs; client-side end>=start validation; new Playwright spec. Plan: `docs/superpowers/plans/2026-05-28-manual-entry-form.md` |
| 24 | Per-entry location & time | `Photo.lat/lon` columns (existing); `_resolve_lat_lon` in `workers/enrichments.py` (photo EXIF → diary fallback) | Add `entries.lat`, `entries.lon`, `entries.tz_override` via Alembic migration; update `_resolve_lat_lon` to check entry first; expose fields in `EntryCreate`/`EntryPatch`/`EntryOut`; Open-Meteo Geocoding city-search field on manual entry form; per-entry `tz_override` used for weather time window. Depends on 16 + 23 (done). Unblocks P-6 (map view). |

### Item 25 — code review scope

The review covered the whole codebase on `main`, not a single diff: `apps/api/app`, `apps/web/src`, Alembic migrations, `scripts/` and the deploy files. Results are in [`design/code-review-2026-11.md`](design/code-review-2026-11.md).

Starting points noted during the 2026-11-11 status audit, before the review (see the review document for current status):

- **Tier model drift** — `services/tier.py` and `routers/v1/diaries.py` still use `free / tier1 / tier2`, and Free is capped at 5 manual + 3 auto entries *lifetime*. The working model in `COMPETITION.md` is Free / Plus / Family with unlimited manual entries and 5 AI drafts *per month*. Resolve as part of item 18.
- **Item 18 scaffold row is stale** — `workers/rules.py` already calls `try_enforce_entry_tier_limit` on the auto-entry path. Still missing: photo-upload gating (`routers/v1/photos.py` has no tier check) and the 403 upgrade prompt on entry creation in the web UI.
- **Item 14 design is out of date** — `routers/v1/integrations.py` requests `photoslibrary.readonly`. Google removed that scope from the Library API on 2025-03-31; calls using it return 403. Reading a user's existing library now requires the Photos Picker API (`photospicker.mediaitems.readonly`), which is user-initiated selection, not background scanning. Item 14 and `design/05-google-oauth-integrations.md` / `design/06-scan-worker.md` need a redesign before any build. Source: https://developers.google.com/photos/support/updates
- **`require_reauth` async-loop bug** — still open, now at `app/core/auth.py:104`; still has no callers.
- **Stale TODO** — `workers/backfill.py:114` `TODO(item-15)` refers to `group_events_into_entries`, which does not exist; entry creation moved to the rules engine.
- **Deploy script bug** — GitHub issue #39 (`scripts/nuc/10-secrets.sh` prepends a newline to every secret; DDNS container stopped on the NUC).
- **Deploy workflow** — `.github/workflows/deploy.yml` fails on every push to `main` with a workflow-file error (0s runtime), separate from the `DEPLOY_ENABLED` gate.
- **Dependencies** — 15 Dependabot PRs (#4–#18) open since 2026-05-22, including major bumps (Next 15→16, TypeScript 5→6, ESLint 9→10).
- **Item numbering collision** — "19" means *diary create wizard* here and *diary sharing + invitations* in `design/09-poc-scope.md` Phase 3.

### File-level coordination

Two files are touched by multiple Wave A–C items — land changes in wave order to avoid conflicts:

- **`workers/tasks.py`** — touched by items 17, 15, 14, 18. Sequential PRs required.
- **`workers/llm.py`** — touched by items 22, 15, 16. Land 22 first (it introduces the abstraction); 15 and 16 then extend it rather than retrofitting.

---

## Notes

- **`NEXT_PUBLIC_GOOGLE_CLIENT_ID`** — wire this up in `apps/web/.env.local` when Google OAuth / Photos integration lands (item 14). Email+password login is sufficient until then.

---

## Known issues and technical debt

See [`design/known-issues.md`](design/known-issues.md).
