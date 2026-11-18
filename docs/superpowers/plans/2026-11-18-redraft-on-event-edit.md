# Redraft on Event Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a stored Google Calendar event's title, location, description or guests change, the draft entry it belongs to is redrafted through the existing free, coalesced `queue_redraft`.

**Architecture:** A pure function compares the stored event payload with the incoming one and names the changed fields. `_ingest_calendar_event` calls it before overwriting the payload. If anything changed and the event still belongs to a live draft of an active owner, it queues one redraft after the session commits and logs the field names. The existing drafting code already decides whether to rewrite in place or store a suggestion, and never writes over published text.

**Tech Stack:** Python, FastAPI workers (Celery), SQLAlchemy async, structlog, pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-redraft-on-event-edit-design.md`

## Global Constraints

- The fields that count: `summary`, `location`, `description`, and guests, where guests are compared as the set of `(displayName, email)` pairs. `responseStatus` and `organizer` are ignored: an RSVP alone is not an edit.
- Compare after `_strip_injection`, as stored.
- Only an entry with `status == "draft"` and `deleted_at IS NULL` is redrafted. Published entries, deleted entries and events with no entry do nothing.
- No redraft when the diary owner's `deletion_state()` (`app/core/auth.py:22`) is not `"active"`.
- Call `queue_redraft(entry_id)` only after the session commits, and swallow and log a queueing failure, as the move path does (`app/workers/tasks.py:410-414`).
- An event moved to the future and edited at the same time: the move path wins. Queue at most one redraft per ingest.
- Log line name: `event_edit_redraft_queued`, with fields `entry_id`, `event_id` and `changed` (a list of field names in the order `summary`, `location`, `description`, `guests`). Never log event text.
- Run `make test-all` before any push. Run the dev-stack check after it, never at the same time (CLAUDE.md).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage explicit paths only.

## Review Focus

1. **Payloads stored by older code**, which may lack `location`, `description` or `attendees`. A missing key must equal `""` or `[]`, or the first re-sync after deploy (a 410 full resync returns every event) redrafts every draft. Test: `test_missing_keys_equal_empty_values` (Task 1).
2. **A full resync with identical data** must queue nothing. Test: `test_identical_rescan_queues_nothing` (Task 2).
3. **An RSVP-only change, or guests reordered,** must queue nothing. Tests: `test_rsvp_change_is_not_an_edit` and `test_guest_order_is_not_an_edit` (Task 1), and `test_rsvp_only_change_queues_nothing` (Task 2).
4. **A move to the future plus an edit** must queue no second redraft for an entry the move already handled. Test: `test_move_to_future_with_edit_queues_at_most_once` (Task 2).
5. **`None` values from Google** (`"description": null`) must count as empty. Test: `test_none_equals_empty` (Task 1).

---

### Task 1: Change detection

**Files:**
- Modify: `apps/api/app/workers/event_changes.py` (add one function)
- Create: `apps/api/tests/unit/test_event_edit_changes.py`

**Interfaces:**
- Produces: `event_edit_changes(old: dict | None, new: dict) -> list[str]`. It returns the changed field names, a subset of `["summary", "location", "description", "guests"]` in that order. `old=None` returns `[]`.

- [ ] **Step 1: Write the failing tests** in `apps/api/tests/unit/test_event_edit_changes.py`:

```python
from app.workers.event_changes import event_edit_changes

BASE = {"summary": "Soccer", "location": "Field 3", "description": "Bring water",
        "attendees": [{"displayName": "Grandma", "email": "g@example.com",
                       "organizer": False, "responseStatus": "needsAction"}]}

def _with(**kw):
    return {**BASE, **kw}

def test_no_change():
    assert event_edit_changes(BASE, dict(BASE)) == []

def test_each_field_is_named():
    assert event_edit_changes(BASE, _with(summary="Soccer final")) == ["summary"]
    assert event_edit_changes(BASE, _with(location="Field 4")) == ["location"]
    assert event_edit_changes(BASE, _with(description="")) == ["description"]

def test_several_fields_in_fixed_order():
    assert event_edit_changes(BASE, _with(description="x", summary="y")) == ["summary", "description"]

def test_guest_added_or_renamed_is_an_edit():
    more = BASE["attendees"] + [{"displayName": "Sam", "email": "s@example.com"}]
    assert event_edit_changes(BASE, _with(attendees=more)) == ["guests"]

def test_rsvp_change_is_not_an_edit():
    rsvp = [{**BASE["attendees"][0], "responseStatus": "accepted", "organizer": True}]
    assert event_edit_changes(BASE, _with(attendees=rsvp)) == []

def test_guest_order_is_not_an_edit():
    two = [{"displayName": "A", "email": "a@x"}, {"displayName": "B", "email": "b@x"}]
    assert event_edit_changes(_with(attendees=two), _with(attendees=list(reversed(two)))) == []

def test_missing_keys_equal_empty_values():
    assert event_edit_changes({"summary": "Soccer"},
                              {"summary": "Soccer", "location": "", "description": "", "attendees": []}) == []

def test_none_equals_empty():
    assert event_edit_changes(_with(description=None), _with(description="")) == []

def test_no_stored_payload():
    assert event_edit_changes(None, BASE) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_event_edit_changes.py -q`
Expected: FAIL with `ImportError` (cannot import `event_edit_changes`).

- [ ] **Step 3: Implement `event_edit_changes`** in `apps/api/app/workers/event_changes.py`. Text fields compare as `str(value or "")`. Guests compare as a `frozenset` of `(displayName or "", email or "")` over the dict elements of `attendees or []`. Add a one-line docstring saying it mirrors the fields the drafting prompt reads.

- [ ] **Step 4: Run to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_event_edit_changes.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/event_changes.py apps/api/tests/unit/test_event_edit_changes.py
git commit -m "feat(api): name the event fields an edit changed (row 33)"
```

---

### Task 2: Redraft on edit at ingest

**Files:**
- Modify: `apps/api/app/workers/tasks.py` (`_ingest_calendar_event`, about lines 368-414)
- Create: `apps/api/tests/integration/test_event_edit_redraft.py`
- Modify: `POC_PHASE2_TODO.md` (row 33 status)

**Interfaces:**
- Consumes: `event_edit_changes(old, new) -> list[str]` (Task 1); `queue_redraft(entry_id: uuid.UUID) -> None` and `log` (`app/workers/tasks.py`); `deletion_state(user) -> "active" | "pending" | "blocked"` (`app/core/auth.py:22`).
- Produces: nothing new for other tasks.

- [ ] **Step 1: Write the failing tests** in `apps/api/tests/integration/test_event_edit_redraft.py`.
  - Reuse the shape of `TestReschedule` in `tests/integration/test_ended_events.py:485-510`: ingest through `_ingest_calendar_event`, with `evaluate_rules_for_event` patched.
  - Build an entry with one attached, ended event, like `_attached` (`tests/integration/test_ended_events.py:197-229`). Give the stored event this payload: `{"summary": "Soccer practice", "location": "Field 3", "description": "Bring water", "attendees": [{"displayName": "Grandma", "email": "g@example.com", "organizer": False, "responseStatus": "needsAction"}]}`. Re-ingest it with the same `id` and past times, and changed fields.
  - Patch `app.workers.tasks.queue_redraft` with `AsyncMock()`, and capture logs with `structlog.testing.capture_logs()`.

  Tests and assertions:
  - `test_title_change_queues_one_redraft`: `redraft.assert_awaited_once_with(entry.id)`. Exactly one log entry has `event == "event_edit_redraft_queued"`, with `changed == ["summary"]`, and `entry_id` and `event_id` as strings.
  - `test_location_description_and_guest_changes_each_queue` (parametrized over the three): one await each, and `changed` names that field (`"guests"` for a guest added).
  - `test_rsvp_only_change_queues_nothing`: `redraft.assert_not_awaited()`, and no `event_edit_redraft_queued` log line.
  - `test_identical_rescan_queues_nothing`: `assert_not_awaited`.
  - `test_published_entry_queues_nothing`: `status="published"`, title changed, `assert_not_awaited`.
  - `test_deleted_entry_queues_nothing`: the entry has `deleted_at` set, `assert_not_awaited`.
  - `test_event_with_no_entry_queues_nothing`: an event stored with `entry=None`, title changed, `assert_not_awaited`.
  - `test_edited_draft_still_queues`: `body_source="user"`, title changed, `assert_awaited_once_with(entry.id)`. That the run stores a suggestion is already pinned by `tests/integration/test_suggestions.py:320` (`test_a_background_redraft_of_the_parents_text_is_a_suggestion`).
  - `test_owner_pending_deletion_queues_nothing`: set the owner's `deleted_at=now` and `hard_delete_after=now + 7 days`, title changed, `assert_not_awaited`.
  - `test_move_to_future_with_edit_queues_at_most_once`: the untouched draft has two events. Move one to `now + 5 days` and change its title. `redraft.await_count <= 1`, and no `event_edit_redraft_queued` line for an entry the event no longer belongs to.
  - `test_queue_failure_is_swallowed`: `queue_redraft` raises `RuntimeError`. The ingest still returns the event id, and the new payload is stored.

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_event_edit_redraft.py -q`
Expected: the tests asserting an await, or the log line, FAIL. The "queues nothing" tests may pass already, which is fine: they guard the change.

- [ ] **Step 3: Implement in `_ingest_calendar_event`**
  - On the `event is not None` branch, compute `changed = event_edit_changes(event.payload, payload)` before `event.payload = payload`.
  - After the move handling, if `changed`, no `entry_to_redraft` was set by the move, and `event.entry_id` is not None:
    - load the entry and its diary's owner in the same session;
    - when the entry is a live draft and the owner is active, record `edit_redraft = (event.entry_id, changed)`.
  - After the session block, beside the existing move-redraft call:
    - `await queue_redraft(entry_id)`, then log `event_edit_redraft_queued` with `entry_id=str(...)`, `event_id=str(event_id)` and `changed=changed`;
    - on exception, `log.exception("edit_redraft_queue_failed", entry_id=...)`.

- [ ] **Step 4: Run to verify they pass, plus the neighbouring suites**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_event_edit_redraft.py tests/integration/test_ended_events.py tests/integration/test_redraft_coalescing.py tests/unit/test_event_edit_changes.py -q`
Expected: all pass.

- [ ] **Step 5: Update tracker row 33**

Set its status to `**built** — branch \`feat/redraft-on-event-edit\`; design \`docs/superpowers/specs/2026-11-18-redraft-on-event-edit-design.md\`` (it is set to done after merge).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/workers/tasks.py apps/api/tests/integration/test_event_edit_redraft.py POC_PHASE2_TODO.md
git commit -m "feat(api): redraft a draft when its calendar event is edited (row 33)"
```

---

## Finish

`make test-all` (full), then the dev-stack check (`make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`), then PR, CI, and merge under Andrew's standing OK. Deploy only with Andrew's OK, after a DB dump. Then Andrew renames an event that has a draft, and the NUC logs show `event_edit_redraft_queued`.
