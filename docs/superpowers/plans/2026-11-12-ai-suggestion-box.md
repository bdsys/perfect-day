# AI Suggestion Box (25.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The AI never overwrites text that is already in an entry. Its new version shows in a box under the text, with Accept and Dismiss buttons. The drafting mode is chosen by who wrote the existing text, not by whether there is any.

**Architecture:** A pending suggestion is stored as five columns on `entries`. `generate_draft_for_entry` gains a `trigger` (`regenerate` or `background`). It picks the mode from `body_source` and then either writes in place or stores a suggestion. Two new endpoints accept or dismiss the suggestion. Publishing clears it. PATCH removes flagged words that are no longer in the text. The blend check gains the word scan it lacks today. The entry page shows the box; a mock-up goes to Andrew before the page is built.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, pytest with testcontainers, Next.js, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-11-code-review-decisions-design.md` section 3.

**Builds on:** this branch (`feat/25.3-suggestion-box`) contains 25.1 (`body_source = 'user'`, migration 0010) and 25.2 (`workers/llm.py` changes). It cannot merge before PRs #41 and #42.

## Global Constraints

- When the entry already has text, the AI never overwrites it. Its version appears in a box under the text field.
- When the text field is empty, the first draft goes straight into the field, as today.
- Machine-only text means `body_source` is `llm` or `fallback`. Text with `user`, `llm_polished` or `llm_hybrid` contains the parent's writing.
- Regenerate on machine-only text produces a fresh events-mode draft with the full word check. Regenerate on text containing the parent's writing produces a blend. Either way the result is a suggestion.
- Accept takes the suggested title as well as the body. Accepting a fresh draft leaves the text marked `llm`; accepting a blend marks it `llm_hybrid`.
- A new suggestion replaces a pending one. Dismiss discards it. Publishing discards it.
- Flagged words stay until the word is no longer in the text or the entry is published.
- Background redrafts replace machine-only text in place and arrive as a suggestion on text containing the parent's writing.
- Published entries get no suggestions.
- Background jobs are queued only after their rows are committed (review finding C-4).
- `make test-all` passes at the end.

## Calls made in this plan (not in the spec), for Andrew to review before building

1. **Storage.** The pending suggestion lives in five nullable columns on `entries`: `suggested_title`, `suggested_body_markdown`, `suggested_flagged_tokens`, `suggested_body_source` and `suggested_at`. One suggestion per entry. (The spec left this to the builder.)
2. **Endpoints.** `POST /v1/entries/{id}/suggestion/accept` and `DELETE /v1/entries/{id}/suggestion`, both returning the entry. Both return 409 `no_suggestion` when nothing is pending. (Left to the builder.)
3. **Text edited while the AI is working.** A draft can take several seconds. If the text changes during that time, a result that would have replaced the text arrives as a suggestion instead.
4. **A failed Regenerate on an entry with text produces no suggestion.** The bullet-list fallback is not offered as a suggestion; the person sees the existing "AI generation failed" message. The fallback is still written in place in the two cases it covers today: an empty field, or a background redraft replacing machine-only text.
5. **Machine-only text with no events left** (for example, an event removed from a picker entry) counts as nothing to draft from and fails with `no_inputs`. Today it would "polish" AI text that has no source left.
6. **Accept keeps earlier warnings.** On Accept the entry's flagged words are the suggestion's flagged words plus any of the entry's existing flagged words that are still in the accepted text. Without this, a blend could clear a warning: the blend check treats the parent's text as a source, and an accepted blend counts as the parent's text.
7. **Polish mode gets the word check too.** Polish mode is the parent's text with no events. It flags words found in neither the parent's text nor the weather. The spec names only the blend check.
8. **Regenerate on a published entry returns 409 `entry_published`.** Today the request is accepted and the worker silently does nothing.
9. **Publishing clears the flagged words as well as the suggestion**, so unpublishing does not bring old warnings back. This is how I read the spec's "until … the entry is published".
10. **A pending suggestion survives edits.** If the parent edits the text after a suggestion arrives, the suggestion stays, and Accept still replaces whatever is in the field.

The look of the box and where it sits (read view only, or also while editing) is decided with the mock-up in Task 6.

## Review Focus

- A person edits the text while a Regenerate is running: their edit must survive and the result must arrive as a suggestion (call 3). Test in Task 3.
- Celery messages queued before this ships carry one argument. `generate_entry_draft(entry_id)` must still run, as a background redraft. Test in Task 4.
- The laundering case: a flagged word in draft one must stay flagged after Regenerate and Accept when it is still in the text (call 6). Tests in Tasks 3 and 5.
- A spanning entry the parent has edited gains a new event: the edit must survive, and a blend must arrive as a suggestion. This is the gap left open by `workers/rules.py:278`. Test in Task 4.
- Accept after the parent edited the text following the suggestion: the field takes the suggestion and `body_source` takes the suggestion's value (call 10). Test in Task 5.

---

### Task 1: Storage and the API shape

**Files:**
- Modify: `apps/api/app/models/__init__.py` (`Entry`)
- Create: `apps/api/alembic/versions/0011_entry_suggestions.py` (`down_revision = "0010"`)
- Modify: `apps/api/app/routers/v1/entries.py` (`EntryOut`, `_entry_out_from_orm`)
- Test: `apps/api/tests/integration/test_suggestions.py` (new)

**Interfaces:**
- Produces: `Entry.suggested_title: str | None`, `Entry.suggested_body_markdown: str | None`, `Entry.suggested_flagged_tokens: list[str] | None` (`ARRAY(Text)`), `Entry.suggested_body_source: str | None` (`String(20)`), `Entry.suggested_at: datetime | None`. Check constraint `ck_entries_suggested_body_source`: `suggested_body_source IS NULL OR suggested_body_source IN ('llm','llm_polished','llm_hybrid')`.
- Produces: `SuggestionOut(title: str | None, body_markdown: str, flagged_tokens: list[str], body_source: str, created_at: datetime)` and `EntryOut.suggestion: SuggestionOut | None`, which is None when `suggested_body_markdown` is None.

- [ ] Test: `GET /v1/entries/{id}` on a new entry returns `"suggestion": null`; after setting the five columns directly in the database it returns them under `suggestion`, with `flagged_tokens` `[]` when the column is NULL.
- [ ] Run it and see it fail.
- [ ] Add the columns, constraint, migration (downgrade drops them) and the schema.
- [ ] Run it and see it pass. Run `alembic upgrade head` then `alembic downgrade 0010` against the test Postgres to check the downgrade.
- [ ] Commit.

### Task 2: The word check for blend and polish

**Files:**
- Modify: `apps/api/app/workers/llm.py` (`validate_citation`)
- Test: `apps/api/tests/unit/test_llm_validator.py` (existing; its current hybrid "no flags" expectations change)

**Interfaces:**
- `validate_citation(output, events, mode, body_seed, enrichments)` keeps its signature. `body_seed` is now the parent's title and body joined by a newline. In `hybrid` mode the scan flags capitalised words (same regex and calendar-word list as events mode) found in neither the cited events, the enrichments nor `body_seed`. In `polish` mode it flags words found in neither `body_seed` nor the enrichments. Index checks are unchanged.

- [ ] Tests, hybrid: a body with `Grandma`, events not mentioning it, seed `"We saw Grandma"` → no flag; a body with `Disneyland` in neither → `["Disneyland"]`; a word in a cited event → no flag; a word only in an *uncited* event → flagged (same as events mode).
- [ ] Tests, polish: seed `"Park day with Sam"`, output mentions `Sam` and `Zoo` → `["Zoo"]`; a word in the weather enrichment payload → no flag.
- [ ] Run, see them fail, implement, see them pass. Run `tests/unit/test_llm_modes.py` (it calls the validator through the worker).
- [ ] Commit.

### Task 3: The worker writes in place or stores a suggestion

**Files:**
- Modify: `apps/api/app/workers/llm.py` (`generate_draft_for_entry`)
- Modify: `apps/api/tests/unit/test_llm_modes.py` (polish/hybrid success tests now expect a suggestion; mode tests use `body_source`)
- Test: `apps/api/tests/integration/test_suggestions.py`

**Interfaces:**
- Produces: `async def generate_draft_for_entry(entry_id: uuid.UUID, trigger: Literal["regenerate", "background"] = "background") -> None`.
- Mode, from the first read: empty body + events → `events`; machine-only body + events → `events`; parent's writing + events → `hybrid`; parent's writing, no events → `polish`; empty or machine-only body, no events → failed `no_inputs`, entry unchanged.
- Target, from the first read: write in place when the body is empty, or when `trigger == "background"` and the body is machine-only. Otherwise store a suggestion.
- At write time (second session), if `body_markdown` or `body_source` differs from the first read, an in-place target becomes a suggestion (call 3).
- Storing a suggestion sets the five `suggested_*` columns (`suggested_body_source` is `llm`, `llm_polished` or `llm_hybrid` by mode; `suggested_at = now`). It does not touch `title`, `body_markdown`, `body_source` or `flagged_tokens`. It records a `success` `LLMGeneration` and bumps `updated_at`, as today, so the page's polling stops.
- Failure with a suggestion target: failed `LLMGeneration`, entry and any pending suggestion unchanged (call 4). Failure with an in-place target: as today (fallback in events mode, unchanged otherwise).

- [ ] Integration tests (real database, mocked provider as in `test_regenerate_endpoint.py`):
  - Regenerate on an untouched AI draft stores a suggestion with `suggested_body_source == "llm"` and leaves `title`, `body_markdown`, `body_source` and `flagged_tokens` byte-for-byte unchanged; the provider received the events prompt (`SYSTEM_PROMPT_EVENTS`).
  - The same after a typo fix (`body_source = "user"`) runs the blend (`SYSTEM_PROMPT_HYBRID`), body unchanged, `suggested_body_source == "llm_hybrid"`.
  - Laundering: an entry with body `"We went to Disneyland."`, `flagged_tokens = ["Disneyland"]`, `body_source = "llm"`, and an event that does not mention Disneyland. Regenerate returns a body that again mentions Disneyland. Expect the entry's `flagged_tokens` still `["Disneyland"]` and `suggested_flagged_tokens == ["Disneyland"]`, which the suggestion's own check found.
  - Empty entry with events, `trigger="regenerate"` → written in place, `suggestion` stays null (first draft).
  - Background on machine-only text → replaced in place, no suggestion.
  - Background on `body_source = "user"` → suggestion; body unchanged.
  - Text edited during generation: the provider mock's `generate` updates the entry's body to `"edited meanwhile"` with `body_source = "user"` before returning; on an empty-body entry expect `body_markdown == "edited meanwhile"` and the draft stored as a suggestion.
  - A failed provider on a suggestion target leaves a pending suggestion from an earlier run unchanged and records a failed generation.
  - A second successful Regenerate replaces the pending suggestion.
  - Machine-only body, no events → failed generation with error `no_inputs`, nothing changed.
- [ ] Run them and see them fail.
- [ ] Implement. Update the existing mode tests in `test_llm_modes.py` whose expectations this changes (polish and hybrid success now store suggestions; "body only" means `body_source = "user"`).
- [ ] Run `tests/unit/test_llm_modes.py tests/unit/test_llm_failover.py tests/integration/test_suggestions.py tests/integration/test_regenerate_endpoint.py` and see them pass.
- [ ] Commit.

### Task 4: Callers say why they are asking

**Files:**
- Modify: `apps/api/app/workers/tasks.py` (`generate_entry_draft`, `_generate_entry_draft`)
- Modify: `apps/api/app/routers/v1/entries.py` (`regenerate_entry`)
- Test: `apps/api/tests/integration/test_suggestions.py`, `apps/api/tests/integration/test_regenerate_endpoint.py`

**Interfaces:**
- Produces: Celery task `generate_entry_draft(entry_id: str, trigger: str = "background")`, passing `trigger` through. An unknown value is logged and treated as `background`.
- `regenerate_entry` queues `generate_entry_draft.delay(str(entry.id), trigger="regenerate")` and returns 409 `entry_published` for a published entry (call 8).
- Unchanged: `workers/rules.py`, the cancel and reschedule redrafts in `tasks.py`, and `routers/v1/calendar_events.py` all keep calling `.delay(entry_id)` and so run as background. The rules engine's spanning regroup is covered by the target rule in Task 3.

- [ ] Tests: the endpoint queues with `trigger="regenerate"` (assert the `delay` mock's call); the endpoint on a published entry returns 409 with `error.code == "http_error"` and `error.message == "entry_published"`, and queues nothing; `_generate_entry_draft(entry_id)` with one argument runs as background (machine-only body replaced in place).
- [ ] Spanning regroup test: a spanning rule, an entry it created with `body_source = "user"` and body `"Our trip so far"`, a second ended event on the same dates evaluated with `evaluate_event_against_rules`; then run the queued draft. Expect the body still `"Our trip so far"` and a blend suggestion stored.
- [ ] Run, see them fail, implement, see them pass, commit.

### Task 5: Accept, Dismiss, Publish and edits

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py` (`patch_entry`, `publish_entry`, two new endpoints)
- Create: `apps/api/app/services/flagged_words.py`
- Test: `apps/api/tests/unit/test_flagged_words.py`, `apps/api/tests/integration/test_suggestions.py`

**Interfaces:**
- Produces: `still_present(words: list[str], text: str) -> list[str]` in `services/flagged_words.py`: the words that still appear in `text` as whole words (case-sensitive, `\b` boundaries), in their original order, without duplicates.
- `POST /v1/entries/{id}/suggestion/accept` (editor): `title = suggested_title` when that is not None; `body_markdown = suggested_body_markdown`; `body_source = suggested_body_source`; `flagged_tokens = suggested_flagged_tokens + still_present(old flagged_tokens, new title + body)` without duplicates (call 6); clears the five columns. Returns `EntryOut`. 409 `no_suggestion` when none is pending.
- `DELETE /v1/entries/{id}/suggestion` (editor): clears the five columns; returns `EntryOut`; 409 `no_suggestion` when none is pending.
- `publish_entry` also clears the five columns and sets `flagged_tokens = []` (call 9).
- `patch_entry`, when title or body changes, sets `flagged_tokens = still_present(flagged_tokens, title + "\n" + body)`. It leaves a pending suggestion alone (call 10).

- [ ] Unit tests for `still_present`: `(["Sam", "Zoo"], "Sam went home")` → `["Sam"]`; `(["Sam"], "Samantha")` → `[]`; `(["Sam"], "sam")` → `[]`; empty list → `[]`.
- [ ] Integration tests: Accept replaces body and title, sets `body_source` to the suggestion's value and carries its flags; Accept of a blend suggestion with `suggested_title = None` keeps the existing title; Accept keeps an old flag still in the text and drops one that is not; Accept after the parent edited the text (`body_source = "user"`) still replaces it and sets `body_source = "llm"` for a fresh-draft suggestion; Dismiss clears it and leaves the body; Accept and Dismiss with nothing pending return 409 `no_suggestion`; a viewer gets 403 on both; Publish clears the suggestion and the flags; PATCH removing `Disneyland` from the body removes it from `flagged_tokens`; PATCH that changes only the date leaves `flagged_tokens` alone.
- [ ] Run, see them fail, implement, see them pass, commit.

### Task 6: Mock-up for Andrew (stop here)

- [ ] Publish a mock-up of the entry page with a pending suggestion as a private Artifact: the box under the text, the suggestion's own "verify" words, Accept and Dismiss, selectable suggestion text, and how the page reads while a background suggestion is waiting.
- [ ] Ask Andrew to approve it or change it. **Do not start Task 7 until he has.**

### Task 7: The entry page

**Files:**
- Modify: `apps/web/src/lib/api.ts` (`Entry.suggestion`, `entries.acceptSuggestion(id)`, `entries.dismissSuggestion(id)`)
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`
- Test: `apps/web/e2e/suggestion-box.spec.ts` (new)

The details follow the approved mock-up. Fixed by the spec: the suggestion text can be selected and copied; Accept replaces the field's content; the box shows its own flagged words. The status text after Regenerate on an entry with text says a suggestion is ready, not "Draft regenerated".

- [ ] Playwright, in the style of the existing specs: type text into an entry and save, click Regenerate, see the box, select text inside it (assert the selection's text via `window.getSelection()`), click Accept, see the field change to the suggestion. A second test: Dismiss hides the box and leaves the text.
- [ ] Run `make test-e2e`, see it fail, build the page, see it pass. If it fails with "Can't locate revision", reset the test database first (it is bind-mounted at `/tmp/pd-test-postgres`).
- [ ] Commit.

### Task 8: Documents and full verification

- [ ] Update `design/04-llm-integration.md` (modes chosen by who wrote the text; suggestions; blend and polish checks), `design/code-review-2026-11.md` (C-5), `POC_PHASE2_TODO.md` (25.3).
- [ ] `make test-all`.
- [ ] Commit.

**Not covered by automated tests:** how the box reads on a phone-width screen, checked by hand in the dev stack; behaviour with a real model (no live call is planned; the provider is mocked throughout).
