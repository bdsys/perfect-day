# Time and Timezones

## Authoritative timezone

`diaries.timezone` is the source of truth for every date the user sees in that diary. The server timezone is fixed to UTC. `entries.entry_date` and `entry_end_date` are interpreted in `diaries.timezone` — never in server/UTC time.

All worker code that needs "today" or "current date" uses:

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

diary_tz = ZoneInfo(diary.timezone)
today_in_diary = datetime.now(timezone.utc).astimezone(diary_tz).date()
```

Never use `datetime.today()`, `date.today()`, or `now()::date` in worker or migration code without TZ conversion.

## Conversion rules

| Source | How to convert |
|---|---|
| Google Calendar events | Arrive as UTC `datetime` (or RFC 3339 with offset). Convert `start` and `end` to `diary_tz` before computing `entry_date`. |
| Google Photos `creationTime` | UTC. Convert to `diary_tz` before assigning to a photo's candidate `entry_date`. |
| Floating-time events | Google Calendar events with `date` (not `dateTime`) fields have no TZ info. Treat as if they were in `diary_tz`. |
| User-submitted dates (API) | Accept as ISO 8601 date strings; interpret in `diary_tz`. |
| All `created_at`/`updated_at`/`deleted_at` | `timestamptz`, stored in UTC. Never converted for display — frontend formats these in the user's device/browser locale. |

## DST behaviour

- A calendar event on a day that spans 23 hours (spring-forward) or 25 hours (fall-back) still counts as **one day** in the diary's timezone. The grouping algorithm compares dates, not durations.
- A multi-day event that crosses a DST boundary keeps its natural date span. No special handling required; the per-day date conversion handles it automatically.
- DST ambiguous times (e.g. 1:30 AM appearing twice on fall-back night): use the first occurrence (fold=0 in Python `zoneinfo`). Log a warning but do not fail.

## User-facing display

- All entry dates and event times rendered in `diary_tz` with an explicit timezone abbreviation in tooltips (e.g. "Oct 3, 2024 · 2:15 PM PDT").
- Web and mobile clients read `diary.timezone` from the API and apply it on the client side. They must **not** assume the device timezone matches `diary.timezone`.
- When the device and diary are in different timezones, the tooltip clarifies: "Oct 3 in your diary's timezone (PDT) — Oct 4 in your current timezone (AEST)".

## Diary timezone changes

When `PATCH /v1/diaries/{id}` changes `timezone`:

1. **Existing `entry_date` values are not rewritten.** The stored dates stay as-is; the interpretation context shifts.
2. This can cause a visible jump: entries whose event times straddle midnight in the new timezone may appear to shift one day. Show the user a warning: "Changing the timezone may shift which day some entries appear on."
3. No migration is run; this is a config change, not a data migration. Document that restoring the previous timezone undoes the shift.

## Database conventions

| Column type | Convention |
|---|---|
| `created_at`, `updated_at`, `deleted_at`, `hard_delete_after` | `timestamptz` — UTC in DB, converted at read time by client |
| `entry_date`, `entry_end_date` | `date` — diary-local date; meaning depends on `diaries.timezone` |
| `last_scan_at`, `next_scan_after` | `timestamptz` — UTC; used for scheduling math, not display |
| `quiet_hours_start`, `quiet_hours_end` | `time without time zone` — interpreted in `notification_preferences.timezone` |

**Rule:** if a column is ever displayed to the user as a date, it must be a `date` type or explicitly converted on display. `timestamptz` columns are for internal bookkeeping.

## Test requirements

Per `design/testing.md`: integration test fixtures must include at least one diary with `timezone = 'America/Los_Angeles'` (UTC-8/UTC-7) to catch off-by-a-day regressions. Specific cases to cover:

- An event at 11 PM UTC on Day N → should appear on Day N+1 in `America/Los_Angeles` (PT is Day N, but UTC is Day N+1 so the correct answer is Day N in PT).
- An event at 11 PM UTC on Nov 2 → fall-back night in PT; must not duplicate or skip a day.
- Diary timezone change with an existing entry whose event is near midnight.
- Backfill that crosses a DST boundary.
