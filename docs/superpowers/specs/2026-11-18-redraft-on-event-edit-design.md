# Redraft when a calendar event is edited (row 33)

Approved by Andrew on 2026-11-18 ("Yes, that's what I want"). This is a small change, so this is the short design.

## Why

When Andrew renames a Google Calendar event, or changes its place, notes or guests, the AI draft for that day
keeps describing the old event. Today only two event changes redraft an entry: a move to a future time
(`apps/api/app/workers/tasks.py:385-389`) and a cancellation (`:464-470`).

## What the parent sees (approved)

- After an edit to an event's **title, location, description or guests**, the entry's draft catches up within about
  10 minutes (the existing redraft coalescing window).
- **A draft only the AI wrote** is rewritten in place.
- **A draft the parent edited** keeps their text; the new version appears in the AI suggestion box beside it
  (row 25.3 behaviour).
- **Published entries never change.**
- **An RSVP alone** (a guest answering yes, no or maybe) does nothing.
- These redrafts are **free**: they don't count against the monthly AI drafts (row 18.1 decision).

## Approaches considered

Which entries an edit touches:

- A: AI-only drafts only. Rejected: an edited draft could keep the old event name, and the parent would never know.
- **B: AI-only drafts are rewritten, edited drafts get a suggestion, published entries are left alone.**
  Andrew picked B on 2026-11-18. Drafts are where review happens; a published entry is finished.
- C: B, plus suggestions on published entries. Rejected: it reopens entries the parent has signed off.

How to handle row 33's "it's a new LLM cost path, so measure on the NUC first" (my call; Andrew handed me the how):

- **Build it and log every redraft an edit queues.** The log is the measurement. Two accounts use the NUC today,
  edits within 10 minutes coalesce into one redraft, and a busy week costs cents. If the cost is ever too high,
  the change comes back out in a deploy.
- Rejected: detection-only for a week, then a second release (two releases and a week's wait for little gain);
  an on/off setting (a setting and its tests for something that may never be used).

## Design

- **Where:** `_ingest_calendar_event` in `apps/api/app/workers/tasks.py`, on the branch that updates an existing
  event (`event is not None`), before `event.payload` is replaced.
- **What counts as an edit:** a difference between the stored payload and the new one in `summary`, `location`,
  `description`, or the guests, where the guests are compared as the set of `(displayName, email)` pairs.
  `responseStatus` and `organizer` are ignored, so an RSVP alone is not an edit. These are the event fields the
  drafting prompt reads (`apps/api/app/workers/llm.py:179-197` for guests). Compare after `_strip_injection`, as
  stored, so the same text never looks changed.
- **Which entries:** the event's entry (`event.entry_id`), only when that entry is `status == "draft"` and not
  deleted. An event with no entry does nothing. Published and deleted entries do nothing.
- **How:** `queue_redraft(entry_id)` after the session commits, exactly as the move and cancel paths do. The
  existing drafting code already decides in-place versus suggestion (`apps/api/app/workers/llm.py:548-551`,
  `write_in_place`), and already never writes over published text.
- **A move to the future and an edit at once:** the existing move path wins (it may release the event from an
  untouched draft). Don't queue a second redraft for the same entry; `queue_redraft` would coalesce it anyway.
- **Accounts waiting for deletion:** don't queue for an entry whose diary owner isn't active (`deletion_state()` in
  `apps/api/app/core/auth.py`). My call: cheap here, and it keeps this new path out of backlog row 53.
- **Log:** one structlog line per queued redraft, `event_edit_redraft_queued`, with `entry_id`, `event_id` and
  `changed` (the field names only, e.g. `["summary", "guests"]`). Never the event text: it can carry family details.
  A failure to queue is logged and swallowed, as the move path does.

## Non-goals

- An event moved to a different time that's still in the past (no redraft today either; separate fix if wanted).
- Changes to an event's colour, reminders, conference link or other fields the prompt doesn't read.
- Published entries.
- Backlog row 53 (queued drafts for deleted accounts) beyond this new path.

## Verification

- Integration tests through `_ingest_calendar_event` with a stored event attached to an entry:
  - Each of title, location, description and added guest queues one redraft.
  - An RSVP-only change, and a re-scan with identical data, queue nothing.
  - A published entry and an event with no entry queue nothing.
  - An edited draft (`body_source` not machine-only) still queues, and the drafting run stores a suggestion
    rather than overwriting.
  - An owner pending deletion queues nothing.
  - The log line carries the field names and no event text.
- `make test-all`, then the dev-stack check, then CI.
- After deploy, Andrew renames an event that has a draft and checks that the draft catches up within about
  10 minutes. Then edit a draft he wrote and check a suggestion appears. The NUC logs show `event_edit_redraft_queued`.
