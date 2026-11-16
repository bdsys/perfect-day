# POC Phase 2 — Master Todo

Status tracker for Phase 2. Full feature specs live in [`design/09-poc-scope.md`](design/09-poc-scope.md) — this file tracks only what's done and what's left.

---

## Build order

Items must be built in wave order. Within a wave, items are independent and can run in parallel.

### Re-entry — do before resuming feature work
| # | Feature | Status |
|---|---|---|
| 25 | Full code review of everything shipped so far (Phase 1 + Phase 2 items 13, 15, 16, 17, 19, 22, 23) | **done** — review fixes merged; open findings tracked in `design/code-review-2026-11.md` |

Added 2026-11-11 after a five-month gap. Findings, what was fixed and what is still open are in [`design/code-review-2026-11.md`](design/code-review-2026-11.md). Before starting items 24, 14 and 18: commit and merge the fix branch.

The six open questions were answered on 2026-11-11. The decided designs and their build order are in [`docs/superpowers/specs/2026-11-11-code-review-decisions-design.md`](docs/superpowers/specs/2026-11-11-code-review-decisions-design.md); they are approved in conversation and not yet built:

| # | Follow-up | Status |
|---|---|---|
| 25.1 | Scan and rules: entries only for ended events, cancellations and reschedules applied, one entry per event | **done** — PR #41, merged 2026-11-12 |
| 25.2 | Drafting model: `claude-opus-5-5` as a setting, call-site fixes, weekly model check | **done** — PR #42, merged 2026-11-12 |
| 25.3 | AI suggestion box (Regenerate never overwrites existing text) | **done** — PR #43, merged 2026-11-12; Regenerate on published entries in PR #44 |
| 25.4 | Self-updating NUC: CI builds images, NUC pulls on a timer, SSH deploy removed (Andrew, 2026-11-16: keep in backlog). Interim since 2026-11-16: `40-update.sh` builds the images on the NUC and keeps the previous ones for `50-rollback.sh` (finding P-2) | backlog |
| 25.6 | **NUC go-live — next** (Andrew, 2026-11-16). Make the NUC serve this week's code over real HTTPS, then run 14.1 on a phone. In order: (1) **P-9: first-level hostnames.** Names: `perfectday.andrewlass.com` (web), `perfectday-api.andrewlass.com` (API), `perfectday-media.andrewlass.com` (reserved, P-10). Edge done and verified 2026-11-16 (DNS, wildcard origin certificate expiring 2027-11-16, FortiGate policy 35 on the HTTPS VIP in proxy mode, TCP health check); code and docs in the P-9 PR. Still to do at deploy: Google OAuth redirect URI, NUC `app.env` and DDNS config, web rebuild, delete the old DNS records and the `perfectday-nuc` port-forward VIP, restrict policy 35 to Cloudflare IPs; (2) 25.4 / P-2: deploys must ship new images; (3) P-10 / P-7: photo links reach MinIO from outside, and a MinIO image that still exists; (4) P-11: the bucket script; (5) then 14.1. Findings in `design/code-review-2026-11.md` (P-2, P-7, P-9, P-10, P-11) | **next** |
| 25.5 | Delete test photos, remove old-format photo reading code | pending |
| 26 | Automatic entries with no rule-writing — **needs its own design first** | backlog |
| 27 | One AI button whose label follows the text: "Create event description with AI" for empty or short text, "Polish event description with AI" once a person has written more than 5 words (Andrew, 2026-11-12). Count only the parent's words, so an untouched AI draft still gets a fresh draft | backlog |
| 28 | Local LLM first: try Andrew's local Hermes gateway before Anthropic, then Gemini (Andrew, 2026-11-12). Fits as a third `LLMProvider` in `workers/llm_providers.py`, first in the failover order. **Open before designing:** the gateway's address and API shape (OpenAI-compatible or not), which model it serves, whether the NUC can reach it, and whether a local model keeps to "facts from the source data only" well enough. Test that with the existing validator on real entries before making it the default | backlog |
| 29 | **Account settings page** (Andrew, 2026-11-14). The web app has no account page; account pieces are designed but scattered: account endpoints (`design/03-api-surface.md:9-34`), the 7-day deletion flow with emailed restore link (`design/08-security-privacy.md:106-121`, `design/dns-and-email.md:108-109`), notification preferences (`design/07-notifications.md`, item 20), Google connect/disconnect (today on diary and entry pages). One page: who you are signed in as, Google connection, notification preferences, log out everywhere, delete account with restore. Includes C-14 / TR-3 (restore only reachable for 15 minutes). **Needs its own design first**; open question: restore by login, by emailed link (the documented design), or both | backlog |
| 30 | Upgrade to Python 3.14 (Andrew, 2026-11-14): the API image (`apps/api/Dockerfile`), the three CI jobs and both lock files move together; first check every locked package installs and passes on 3.14. Not urgent: 3.12 has security fixes until 2028-10 (<https://devguide.python.org/versions/>) | backlog |
| 31 | Scans ingest in process (2026-11-14 triage follow-up): `sync_calendar` still hands each fetched event to Celery (`ingest_calendar_event.delay`), so a scan's `entries_created` misses events it just fetched (the panel says "N events checked") and new events are evaluated twice (safe only because of the row lock). Ingest with `queue_evaluation=False` and let the scan's sweep evaluate, as backfill does. Watch the scan lock and the NUC's 2 worker slots | backlog |
| 32 | **Temperature unit setting, °F by default** (Andrew, 2026-11-15). One account-wide choice, °F or °C, defaulting to °F. Today the weather is stored in °C (`workers/open_meteo.py:132-133`, `temperature_max_c`/`temperature_min_c`), and the entry badge shows a bare "21°" with no unit (`components/WeatherBadge.tsx:14-16`), so a US parent reads Celsius as Fahrenheit. The prompt gets the raw °C JSON (`workers/llm.py:253-255`), so AI text can say "21°C". Scope: (1) keep storing °C; (2) the badge converts and shows the unit ("70°F"); (3) the prompt gets values already in the owner's unit, so the "facts from the source data only" word check still matches the numbers. Open: whose unit an editor sees (the owner's or their own), and whether text already drafted keeps its old unit. Belongs on the account settings page (item 29); until that exists it could be a field on `users` with a default of `F` | backlog |
| 33 | **Redraft when an event is edited** (Andrew, 2026-11-15, split out of 18.1). Today only a move to a future time and a cancellation redraft an entry (`workers/tasks.py:385-389`, `464-470`). An edit to a Google event's title, location or description leaves the draft's text as it was. Needs change detection on ingest (compare the stored payload's title, location and description), then `queue_redraft` from 18.1, so it is free and coalesced. It's a new LLM cost path, so measure on the NUC first | backlog |
| 34 | **Event invitations** (birthday parties and other events) (Andrew, 2026-11-15). **Needs its own design first.**<br>**What it does:**<ul><li>A parent creates an event invitation and shares one link that anyone can open. Attendees may be on any plan, or have no account if the design allows it.</li><li>Attendees can RSVP.</li><li>A gift wishlist, or an explicit "no gifts, please".</li><li>Dietary requests.</li><li>Polls for catering and any other question the parent wants to ask attendees.</li><li>Attendees upload their own photos of the event. The parent keeps them and chooses to post them to the diary or do nothing with them.</li></ul>**Plan limit:** Free gets 1 invitation a month. Add an "Invitations" row to `COMPETITION.md`, and a new `LimitSource`.<br>**Open before designing:**<ul><li>Can someone with no account RSVP and upload, or must they sign up (a free account)?</li><li>Link security: unguessable tokens, expiry, revoking a leaked link, rate limits.</li><li>Guest photos are of other people's children too. They need the same encryption at rest, a parent approval queue before anything shows, and deletion when the event is deleted.</li><li>Abuse and storage: upload size and count caps on the NUC.</li><li>Who sees the RSVP list and dietary answers.</li><li>Email for invitations and reminders (item 20, notifications).</li><li>The link puts a new public page on the internet. It needs a WAF and rate-limit review at the Cloudflare/FortiGate edge.</li></ul> | backlog |
| 35 | **Separate Google Cloud project for production** (Andrew, 2026-11-16). Today one project (ID `perfect-day-dev-514922`, in Andrew's new Google account), serves both localhost and the NUC, with two Web OAuth clients (`Perfect Day local`, `Perfect Day NUC`); they share one consent screen, one publishing status, one test-user list and one 100-user cap. Split prod into its own project when that sharing starts to matter. Steps per project: `docs/local-dev.md` § Google Cloud. Related decision, due after the PoC 14.1 phone checks: stay in **Testing** (Calendar and Photos consent expires every 7 days, <https://support.google.com/cloud/answer/15549945>) or go **In production** unverified (unverified-app screen, 100 new users lifetime cap, <https://support.google.com/cloud/answer/7454865>; Google does not say whether the 7-day expiry stops; a public app using the Photos APIs needs Google's review, <https://developers.google.com/photos/overview/configure-your-app>) | backlog |
| 36 | **NUC backups to Andrew's NAS (192.168.1.112): the database and the photos** (Andrew, 2026-11-16). Today nothing is backed up: `scripts/nuc/30-backup.sh` covers Postgres only (to Backblaze B2 via rclone), never includes MinIO photos (finding D-6), and its timer is not installed on the NUC (checked 2026-11-16). **What it needs:** a scheduled `pg_dump` plus a copy of the MinIO photo objects to the NAS on the home LAN, with retention, a failure alert, and a tested restore. **Back up the keys separately:** photos and Google tokens are encrypted with `MASTER_SECRET` from `/etc/perfect-day/app.env`, so a NAS copy without that key cannot be restored, and the key must not sit next to the backups unprotected. **Open before design:** how the NAS is reached (SMB, NFS, rsync over SSH), credentials on the NUC, how many days to keep, whether B2 stays as an off-site copy. **Needs its own design first** | backlog |

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
| 24 | Per-entry location & time (lat/lon + tz_override) | **done** — PR #48, merged 2026-11-12; spec `docs/superpowers/specs/2026-11-12-per-entry-location-design.md` |

16's backfill extension requires 17 done first.

### Wave C — requires Wave B complete
| # | Feature | Status |
|---|---|---|
| 14 | Fast photo adding on drafts (was "Google Photos grant + scan"), plus review fixes S-5, S-7, S-8, C-22 | **merged** to `main` with PR #51 on 2026-11-15 (Andrew's call) **before its real-phone checks — those are still owed, see 14.1 below**. Design `docs/superpowers/specs/2026-11-13-photos-on-drafts-design.md`. After it, one triage session with Andrew on the review's remaining open findings. Google removed library access on 2025-03-31; automatic photos moved to the mobile app (`design/11-mobile-app.md` M-1) |
| 18 | Tier enforcement | **merged** — PR into `feat/14-photos-on-drafts`, then to `main` with PR #51 (2026-11-15). Design `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md`; rulings and deferred minors in that PR's description |
| 18.1 | **Tier follow-ups** (Andrew, 2026-11-15): fix the parent-visible items: raw "tier_limit" text, read-only controls left enabled, a stuck backfill spinner, a draft lost to a killed worker, the reset-date format, the restore-page plan name, and stale AI text when an entry loses all its events. Build the decisions: **background redrafts are free** and **backfill is Plus/Family only**. Inviting stays allowed on read-only diaries. NUC performance items are deferred; the rest is backlog. Full list: `design/tier-followups-2026-11.md`. Design: `docs/superpowers/specs/2026-11-15-tier-followups-design.md` | **merged** — PR #54 (2026-11-16). Follow-ups merged the same day: PR #55 (a moved or deleted event never trashes a draft the parent added photos to; a draft whose event is gone shows "moved or removed" note) and PR #56 (restart policies; integration tests kept off the dev Redis). Deploy: `make migrate` (0018), then restart the API, the worker and beat straight away (the sweeper runs from Celery beat, a separate service; an old worker could write a success row without its reservation key, refunding at most one draft) |
| 14.1 | **Real-phone checks for items 14 and 18 — still to do as part of the PoC** (Andrew, 2026-11-15). #51 merged without them. Run on the NUC over the real HTTPS domain: (1) iPhone Safari / Android Chrome pick several photos, upload keeps GPS; (2) Google Photos picker opens from the phone and you get back to the tab; (3) a HEIC photo shows full-size; (4) Google Cloud: Photos Picker API enabled and picker scope on the consent screen (`docs/google-photos-picker-setup.md`), then read the real `google_photos_refused` reason or confirm it is gone; (5) live Google: does `baseUrl=d` redirect, real `expireTime` format, revoked grant 401 vs 403; (6) a 50 MB upload through Cloudflare + FortiGate to the `api.` host; (7) a portrait iPhone JPEG attached by the owner shows upright to a co-parent full-size (`exiftool -MPF:all`); (8) iPhone Safari: Connect Calendar / Google Photos, press Back on Google's screen, the button works again; (9) disconnect Google during a backfill shows the plain message; (10) item 18 on a phone: a Free account sees the AI-drafts badge and reset date, grayed AI button at 0, and only 1 photo can be picked per entry. Full checklist in PR #51's description | pending |
| 14.2 | **Fix Google Photos "Search Google Photos" saying "isn't connected" — still to do as part of the PoC** (found 2026-11-14; deferred by Andrew 2026-11-15). Connecting Google Photos reports success, then starting a pick says it isn't connected. Likely cause (not yet confirmed against real Google): the Photos Picker API was never enabled in the Google Cloud project, so Google answers 403. Since Plan B, a 403 meaning "API off" shows "Google Photos isn't set up for this app yet" and logs `google_photos_refused code= status= reason=` (`services/google_photos.py`). To do: (1) enable the **Google Photos Picker API** and add the `photospicker.mediaitems.readonly` scope in `perfect-day-dev` now and `perfect-day-prod` before the 14.1 phone checks, per `docs/google-photos-picker-setup.md`; (2) reproduce a pick and read the `google_photos_refused` reason; (3) if picks still fail with the API on, fix the cause (e.g. a missing grant vs revoked grant) | pending |

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
| 14 | Fast photo adding on drafts | Photo upload and `entry_photos` attachment (item 13); photo GPS sets the entry's place (item 24) | **built** — raw-body upload (`POST /v1/photos`), several photos at once from the phone, library picker, Google Photos Picker import (`photo_imports`, `process_photo_import`), HEIC display copies, shared-photo reads (C-22), Google connect cookie (S-5) and revoke (S-8). Left: real-phone checks, then merge |
| 18 | Tier enforcement | Built 2026-11-15. `services/tier.py` (limits) and `services/plan_view.py` (owner's plan), `ai_draft_spends` (monthly usage, migration 0017), gates on diary create, Regenerate, worker auto-drafts and photo attach/import; plan badge and `/plans` in the web UI. | Payments (Stripe) and real upgrades are billing work; `/plans` says "Paid plans are coming soon." |
| 19 | Diary create wizard + edit settings | `PATCH /v1/diaries/{id}` endpoint + `DiaryPatch` schema; `subject_name`/`subject_relation` already in `DiaryOut` | **done** — merged in PR #38 (2026-06-10). `/diaries/new` and `/diaries/[diaryId]/settings` pages, `api.diaries.patch()`, `voice_override`/`tone_hint` on `DiaryCreate`/`DiaryOut`, Playwright spec `diary-create-settings.spec.ts`. |
| 23 | Manual entry form | `"New entry"` button + `handleNewEntry` (hardcodes today); `.popover` CSS class; backend `EntryCreate` accepts `entry_end_date` | **done** — Widen `entries.create` type to include `entry_end_date`; popover form with date/end_date/title inputs; client-side end>=start validation; new Playwright spec. Plan: `docs/superpowers/plans/2026-05-28-manual-entry-form.md` |
| 24 | Per-entry location & time | Built 2026-11-12. The May scaffold notes are superseded by the spec (`docs/superpowers/specs/2026-11-12-per-entry-location-design.md`) and the plan (`docs/superpowers/plans/2026-11-12-per-entry-location.md`). Unblocks P-6 (map view). |

### Item 25 — code review scope

The review covered the whole codebase on `main`, not a single diff: `apps/api/app`, `apps/web/src`, Alembic migrations, `scripts/` and the deploy files. Results are in [`design/code-review-2026-11.md`](design/code-review-2026-11.md).

Starting points noted during the 2026-11-11 status audit, before the review (see the review document for current status):

- **Tier model drift** — resolved by item 18: tiers are `free / plus / family`, manual entries unlimited, Free has 5 AI drafts per month.
- **Item 18 scaffold row** — replaced when item 18 was built.
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
