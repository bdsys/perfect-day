# POC Phase 2 — Master Todo

Status tracker for Phase 2. Full feature specs live in [`design/09-poc-scope.md`](design/09-poc-scope.md) — this file tracks only what's done and what's left.

---

## Build order

Items must be built in wave order. Within a wave, items are independent and can run in parallel.

### Wave A — independent, no prerequisites
| # | Feature | Status |
|---|---|---|
| 22 | Gemini fallback for LLM | **done** |
| 17 | Backfill — slice 1 (Calendar-only, spec-compliant) | pending |
| 15 | Multi-day entry support | pending |

Build 22 first within Wave A — it introduces the LLM provider abstraction that 15 and 16 build on.

### Wave B — requires Wave A complete
| # | Feature | Status |
|---|---|---|
| 13 | MinIO + photo upload | pending |
| 16 | Weather enrichment (Open-Meteo) | pending |

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
| 16 | Weather enrichment | `Enrichment` model | Open-Meteo client (no API key needed), per-entry enrichment call at draft generation, backfill chunk extension |
| 14 | Google Photos | OAuth scaffolding reusable from Calendar (same `oauth_tokens` table, encryption, partial-grant pattern) | Photos scope + grant flow, `ingest_photo` worker, metadata-first filter, `entry_photos` attachment, backfill extension |
| 18 | Tier enforcement | `services/tier.py` with `enforce_entry_tier_limit`; wired into manual-entry router paths | Not called from worker auto-entry path (`workers/tasks.py`); not gating photo uploads; 403 + structured error in UI |

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
