# Scan / Automation Worker

## Layout

Celery + Redis. Beat scheduler triggers periodic dispatch tasks; workers process them. Celery `group`/`chain` for fan-out.

**Worker placement (hybrid deployment):** in the hybrid topology (NUC + CX21 cloud edge), the Celery worker and beat scheduler stay on the NUC. This is required because: (1) all task writes go to the Postgres primary, which is on the NUC; (2) photo ingestion writes encrypted chunks to Cloudflare R2 and must update `photos.dek_ciphertext` in the primary; (3) LLM generation reads photo DEKs via `master_secret`, which is only on the NUC in default mode. The CX21 hosts only the API public ingress and read-replica — no Celery worker runs there. See `deploy/hybrid.md` for the full hybrid topology.

```
Celery Beat (master scheduler)
  ├── every 5 min:  dispatch_due_scans()
  ├── every 6 hours: sweep_orphaned_photos()
  ├── every 1 hour:  process_hard_deletes()   (account/diary grace expirations)
  └── every 1 hour:  cleanup_expired_invites_and_magic_links()

Celery Worker pool
  ├── scan_diary(diary_id)
  ├── ingest_calendar_event(event_id)
  ├── ingest_photo(photo_id)
  ├── group_events_into_entries(diary_id, scan_run_id)
  ├── generate_entry_draft(entry_id)            (see 04-llm-integration.md)
  ├── notify_draft_ready(entry_id)              (see 07-notifications.md)
  ├── render_export(job_id)                     (deferred)
  └── delete_user_hard(user_id)                 (see 08-security-privacy.md)
```

## Beat dispatch

`dispatch_due_scans()` runs every **5 minutes**. Enqueues `scan_diary(diary_id)` for each diary whose `next_scan_after <= now()`. Worst-case dispatch latency for a 60-minute scan interval is 65 minutes (scan interval + up to one dispatch cycle). This is acceptable; document it so on-call isn't surprised when "1 hour" scans arrive up to 65 minutes apart.

## Scan loop

```
scan_diary(diary_id):
  1. Acquire Redis lock `scan_lock:{diary_id}` with **30-min TTL** plus a heartbeat thread inside the task that renews the lock every 5 minutes. If the worker crashes, the TTL self-releases. If the scan legitimately runs longer than 30 minutes, the heartbeat keeps the lock held. (The previous 10-min TTL was shorter than the worst-case scan duration and could double-trigger LLM generation.)
  2. Load diary + scan_jobs row. Bail if scan_enabled=false or
     next_scan_after > now() (backoff still active).
  3. Open scan_runs row (status=running).
  4. For each enabled source (silently skipped if scope absent per OAuth doc):
       a. Calendar via incremental sync (last_calendar_cursor / Google syncToken).
          Normalize each event into events row (source='google_calendar',
          external_id=google_event_id) with payload jsonb. Update cursor on
          success. Filtered by diary_calendar_filters.
       b. Photos via Google Photos API mediaItems list, date-range + metadata-first
          filter. New photos: insert photos row + queue ingest_photo
          (download bytes, thumbnail, MinIO upload). Update last_photos_cursor.
  5. group_events_into_entries(diary_id, scan_run_id).
  6. For each new/updated draft Entry: queue generate_entry_draft(entry_id).
  7. Update scan_jobs.last_scan_*; close scan_runs row.
  8. next_scan_after = now() + scan_interval_minutes (or backoff value).
  9. Release lock.
```

## From calendar event to entry

Built 2026-11-11 as follow-up 25.1; the decisions are in [`docs/superpowers/specs/2026-11-11-code-review-decisions-design.md`](../docs/superpowers/specs/2026-11-11-code-review-decisions-design.md) section 1 and the build notes in [`docs/superpowers/plans/2026-11-11-scan-and-rules-ended-events.md`](../docs/superpowers/plans/2026-11-11-scan-and-rules-ended-events.md).

Entries come from three places: auto-creation rules, the event picker, and manual creation. The scan itself never creates an entry.

**Stored for every event** (`events` table): `occurred_at` (start), `ends_at`, and `rules_evaluated_at`. An all-day event runs from local midnight to local midnight in the diary's timezone. An event with no `ends_at` (non-calendar sources) counts as over.

**An entry is only created once the event has ended.**

1. Ingest (`_ingest_calendar_event`) stores or updates the event. A new event that is already over is sent to the rules straight away. A future event is stored and nothing else happens.
2. Each scan then calls `queue_due_rule_evaluations`, which sends to the rules every stored event that is unattached, not yet evaluated, and now over. With hourly scans an entry appears up to one scan interval after the event ends.
3. Rules run once per event. `rules_evaluated_at` is set whether or not anything matched, so a rule created later does not sweep up old events. "Apply to past N days" on the rules page is the explicit way to do that, and it also skips events that have not ended.

**One entry per event** (`evaluate_event_against_rules`). All enabled rules are checked, oldest first (`created_at`, then `id`). Every matching rule is recorded against the entry (`entry_rule_matches`). When they disagree about grouping, the oldest matching rule decides:

| Rule options | Grouping |
|---|---|
| `recurring: per_series`, event is part of a series | one entry for the whole series, tracked by a `rule_series_claims` row |
| `multi_day: spanning` | joins an existing rule-created entry with exactly the same date range, else a new entry with that range |
| otherwise | a new single-day entry |

A rule that matches but does not decide creates no series claim. There is no ordering control in the UI.

**Cancellations** (`cancel_calendar_event`). Google reports a deleted event as `status: cancelled`, in an incremental sync with only its id. The event row is always deleted. What happens to its entry:

| Entry | Result |
|---|---|
| none | nothing else |
| untouched AI draft made by a rule, no other events | moved to trash; its series claim is deleted |
| untouched AI draft made by a rule, other events remain | kept and redrafted |
| edited by a person, published, or made from the picker | left exactly as it is |

"Untouched AI draft" means `status = 'draft'` and `body_source` is `llm` or `fallback`. Editing the title or body in the app sets `body_source = 'user'`.

**Reschedules.** Re-ingesting an event updates its stored times. If it now ends in the future, the same table applies except the event row is kept, and it is evaluated again after its new end.

**The event picker** lists only unattached events that have ended; `POST .../entries/from-event` refuses a future event with `409 event_not_ended`.

**Photos** (item 14, not built): the earlier plan grouped photos into entries by `taken_at` within the entry's date range or within 3 days of it, narrowest range first, ties broken by `entry_date`, `created_at`, `id`. Item 14 needs a redesign around the Photos Picker API before any of that is built.

## Idempotency

1. `events.external_id` UNIQUE prevents duplicate calendar event ingestion.
2. `photos.external_id` UNIQUE prevents duplicate photo ingestion.
3. Per-task `task_key` Redis marker (24h TTL) protects against Celery retries re-running completed work.

## Backoff and rate limits

```
on Google API 429:
  if Retry-After header present: wait that long
  else: wait 2 ** attempt seconds (1, 4, 16 max)
  retry up to 3 times within the same task

if all retries exhausted with 429:
  scan_jobs.consecutive_failures += 1
  scan_jobs.next_scan_after = now() + min(60 * 2 ** consecutive_failures, 24h)
  log scan_run status = 'partial' (other source may have succeeded)

on success:
  scan_jobs.consecutive_failures = 0
  scan_jobs.next_scan_after = now() + scan_interval_minutes
```

Backoff is **per-diary**, not per-user.

## Backfill mode

`POST /v1/diaries/{id}/scan/backfill` body `{from_date, to_date, sources}`. Worker chunks by week to spread quota:

```
backfill_diary(diary_id, from_date, to_date, sources):
  for week_start in date_range(from_date, to_date, step=7d):
    week_end = min(week_start + 7d, to_date)
    if backfill_runs.status == 'cancelled': break
    if 'calendar' in sources: scan_calendar_chunk(...)
    if 'photos' in sources:   scan_photos_chunk(...)
    group_events_into_entries(...)
    sleep(2s)
```

Hard cap: 365 days. Default cap from OAuth doc is 90 days (`photos_backfill_days_max`). Backfill takes the same `scan_lock:{diary_id}` so it can't collide with scheduled scans.

Cancellation: `DELETE /v1/diaries/{id}/scan/backfill/{runId}` sets `backfill_runs.status='cancelled'`. Worker checks at each **weekly chunk boundary** and exits cleanly — it does not abort mid-week or roll back a partially-completed week. Already-ingested events stay (idempotent re-runs are safe). If a week was already started when cancellation arrives, that week completes before the worker stops.

## Concurrency

Redis lock `scan_lock:{diary_id}` with 10-min TTL. Beat skips diaries that are locked. Manual `/scan/run`, scheduled scans, and backfill all serialize on this lock per diary.

## Quiet hours and notifications

Scans run on schedule regardless of quiet hours — data freshness matters. Only the *notification* is delayed: when a draft is generated during a user's quiet hours window, the notification dispatch task gets `eta=quiet_hours_end` so it fires at the start of the next active window. Default quiet hours: **20:00 → 07:00** in user's timezone.

## Photo handling edge cases

- Photos with no GPS still ingest and attach via timestamp-only matching.
- The metadata-first filter keeps screenshots/selfies out via "rear camera OR has location" check. **This filter applies to Google Photos auto-ingest only.** User-uploaded photos (via `POST /v1/photos/upload-url` → `POST /v1/photos/{id}/finalize`) bypass the filter entirely — the user has explicitly chosen to upload them.
- Photo-only entries (no calendar event) are still **drafts** requiring user review. No exception to the "always drafts first" rule.
- Open-Meteo weather enrichment (per `enrichments` table): before fetching weather for an `entry_date`, the worker checks `enrichments` for an existing row with `kind='weather'` and `entry_id` matching the entry. If found, skip the Open-Meteo API call. This prevents redundant fetches during backfill and retries.

## Failure modes

| Failure | Behavior |
|---|---|
| Google API 5xx | Backoff retry. After 3 failures, scan_run status='partial' or 'failed', `consecutive_failures++`. Next scan delayed exponentially. |
| LLM generation fails for one Entry | Logged in `llm_generations`. Entry stays draft empty-body. Notification fires. Other entries unaffected. |
| Worker crash mid-scan | Celery retries (max 3). Redis lock 30-min TTL self-releases. Idempotency prevents duplicate ingestion. |
| Token refresh fails | `ensure_fresh_access_token` takes a per-`(user_id, provider)` Redis advisory lock (`oauth_refresh:{user_id}:{provider}`, 30-second TTL). The second concurrent worker waits, then re-reads `oauth_tokens` and uses the freshly-stored access token. Without this lock, Google rotates the refresh token on the first call; the second call gets `invalid_grant` and mistakenly marks the integration revoked. If the refresh call itself fails (token expired or revoked at Google), `oauth_tokens.revoked_at` is set, the source is skipped, and a "reconnect Google" notification fires. |
| MinIO unreachable for upload | `photos.finalized_at IS NULL`; orphan sweeper cleans after 24h. |
| User hit tier limit during auto-generation | Events still ingest. `generate_entry_draft` returns early without LLM call. Entry exists with empty body. UI shows "Draft pending — upgrade to generate." |

## Observability

- `scan_runs` table holds per-run audit. Read via `GET /v1/diaries/{id}/scan/runs`.
- Fleet view via `GET /v1/admin/scan-jobs`.
- Structured logs (`structlog`) with `diary_id`, `scan_run_id`, `task_id` on every log line.

## Time and timezones

Full rules are in [`design/time-and-tz.md`](time-and-tz.md). Summary for worker code:

- All date assignment uses `(now() AT TIME ZONE diary.timezone)::date` — never bare `now()::date`.
- Google Calendar events arrive in UTC; convert both `start` and `end` to the diary's timezone before assigning `entry_date` and `entry_end_date`.
- Floating-time events (no TZ in the iCal data) are treated as if they were in the diary's timezone.
- DST: a single-day event spanning 23h or 25h still counts as one day. No special casing.
- Event grouping, backfill chunking, and `dispatch_due_scans` use UTC internally; only the date-assignment step requires diary-timezone conversion.
