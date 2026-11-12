# Code review follow-ups — decided designs

**Date:** 2026-11-11
**Status:** approved by Andrew on 2026-11-11, including the calls made while writing it up. Nothing is built yet.
**Source:** the six questions under "Decisions needed" in [`design/code-review-2026-11.md`](../../../design/code-review-2026-11.md).

Each section separates **what Andrew decided** from **calls made while writing this up**. The second list is the part to check: none of it has been agreed, and any line can be struck.

## The principle behind these choices

Andrew, during the rules discussion: the app has to stand on "magic". Its users are busy parents and it needs to "just work". A complex rules system does not fit that.

Every design below is judged against that, plus the two standing constraints in `CLAUDE.md`: drafts may only state facts found in the source data, and photos of a child are sensitive.

## Work items and order

These are five independent pieces of work plus one backlog item. Suggested order:

| # | Work | Findings closed | Size |
|---|---|---|---|
| 0 | Turn Docker on, run `make test-all` on `chore/code-review-2026-11`, merge it | — | precondition |
| 1 | Scan and rules: ended events only, cancellations, one entry per event | C-6, C-12, C-13 | medium |
| 2 | Drafting model: Opus 5.5 as a setting, plus the call-site fixes | L-1, L-3, L-4 | small |
| 3 | AI suggestion box | C-5 | small feature |
| 4 | Self-updating NUC, no inbound SSH | P-2 | medium |
| 5 | Remove old-format photos and the code that reads them | S-1 follow-up | small |
| — | Backlog: automatic entries with no rule-writing | — | needs its own design |

Items 2 and 3 both change `apps/api/app/workers/llm.py` and `llm_providers.py`; land 2 first. Item 1 changes `workers/tasks.py`, `workers/rules.py` and `workers/calendar_sync.py`; nothing else here touches them. Item 4 is independent and can go at any point.

---

## 1. Scan and rules

### 1a. Entries only for events that have ended (C-6)

**Problem.** Scans read the calendar 90 days ahead (`workers/calendar_sync.py:16`) and every new event goes through the rules at once (`workers/tasks.py:305`). A game three weeks away gets a narrated draft today. A later cancellation is skipped (`calendar_sync.py:95`) and a reschedule does not update the stored time. `design/06-scan-worker.md` says nothing about future events.

**Decided by Andrew**

- Future events are still read and stored, so the event picker and rule preview can see them.
- An entry is only created once the event has ended.
- Cancellations and reschedules are applied to anything not yet published.

**Calls made here**

- "Ended" means the end time has passed. An all-day event has ended when its last day is over in the diary's timezone.
- Rules are evaluated once per event, on the first scan after it has ended. An event that had already ended when it was first seen is evaluated immediately, as today. Creating a new rule does not sweep up old events; "apply to past N days" stays the explicit way to do that.
- Cancelled before any entry exists: the event row is deleted.
- Cancelled after an entry exists: if the entry is an untouched AI draft with no other events, the entry is soft-deleted. If it has other events, the event is detached and the entry redrafted. If a person has edited the entry, or it is published, the event is detached and nothing else changes.
- Rescheduled: the stored time is updated. If it has not ended under the new time, an untouched AI draft created for it is removed and it waits for the new end. Edited or published entries are left alone.
- One-off clean-up when this ships: untouched AI drafts whose only events are still in the future are soft-deleted.
- The event picker only offers events that have ended.
- All-day events get a stored time (C-13). This is a prerequisite: without it "has it ended" cannot be answered for them.

"Untouched AI draft" means `body_source` is `llm` or `fallback`; see section 3.

### 1b. One entry per event (C-12)

**Problem.** When two rules match one event, each creates an entry (`workers/rules.py:160`). The event can belong to only one, so the second is empty, counts against the tier limit and its draft fails. The original plan (`docs/superpowers/plans/2026-05-25-calendar-entry-p3-rules-engine.md:585`) intended a shared entry; the code dropped it and `test_two_rules_matching_same_event` was written to match the code.

**Decided by Andrew**

- One entry per event, with every matching rule recorded against it.
- When matching rules disagree about grouping (per instance or per series, per day or spanning), the oldest rule decides.
- No ordering control in the UI. Rule order is not something a parent should manage.

**Calls made here**

- "Oldest" is by `created_at`, then `id` as a tie-break, so the order is stable.
- Rules that match but do not decide get an `EntryRuleMatch` row and nothing else. A losing per-series rule does not create a series claim.
- `test_two_rules_matching_same_event` is rewritten to expect one entry, two match rows and one queued draft. A second test covers two rules with different grouping options.

**Known cost, accepted:** if a broad rule is created first and a specific one later, the broad one wins and nothing in the UI says so.

**Left to the builder**

- How an event is marked as already evaluated (a timestamp column on `events` is the obvious choice).
- With hourly scans an entry appears up to one scan interval after the event ends. That is accepted.

### Verification for item 1

- Integration tests (need Docker): a future event produces no entry; the same event after its end time produces one; a cancellation before the end leaves no event row; a cancellation after an untouched draft removes the entry; a cancellation after an edited draft leaves the text intact; a reschedule updates the stored time; an all-day event is evaluated the morning after in the diary's timezone, not UTC.
- The rewritten two-rules test and the new different-options test pass.
- `make test-all` passes.
- By hand on dev: add a calendar event for tomorrow matching a rule, trigger a scan, confirm no entry appears; move it to yesterday, scan again, confirm one draft appears.

---

## 2. Drafting model

**Problem.** The model is hard-coded to `claude-haiku-4-5-20251001` (`workers/llm_providers.py:74`). The design doc says Sonnet 4.6 and `CLAUDE.md` says Opus or Sonnet preferred.

**Decided by Andrew**

- Use the current Opus, `claude-opus-5-5`.
- Make it a setting so it can be changed without a code change.
- Do not get caught on a superseded model again.

**Corrections to the discussion, for the record**

- Haiku 4.5 is listed as Active, not deprecated. It is superseded: the current Haiku costs a tenth as much.
- No model ID follows new Opus versions automatically. `claude-opus-5-5` stays Opus 5.5. A self-moving ID would also be wrong here: each generation changes API behaviour and writing behaviour, and drafts about a child should not change model untested.

**Calls made here**

- Setting name `ANTHROPIC_MODEL`, default `claude-opus-5-5`, no date suffix.
- A weekly scheduled task lists the API's models and logs a warning when the configured model is missing or a newer Opus exists. Acting on the warning is a manual step: change the setting, run `make test-live`.
- Effort is set explicitly to `medium` (the model's own default) and revisited only with measurements.
- Call-site changes any Opus 5.5 request needs:
  - raise the output cap from 1,024 to 16,000, because thinking happens before the text;
  - read the first text block of the reply, not `content[0]`;
  - check `stop_reason`; treat `max_tokens` and `refusal` as a failed generation that leaves the entry unchanged;
  - turn on the API's server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`);
  - use the async client, since the call sits inside an async task (L-3).
- The Gemini fallback stays as it is.
- Prompt caching is left alone. The cached part of the prompt is below the minimum cacheable size, so it does nothing, and at this prompt size it would save almost nothing. The "70% cache hits" assumption in `design/04-llm-integration.md` is removed.

**Cost, estimated not measured:** about $0.016 per draft at 1,500 tokens in and 500 out, at $4 / $20 per million tokens. Thinking tokens are billed as output and are not in that figure. Prices are from the Claude API reference bundled with Claude Code, cached 2026-11-03.

**Measured 2026-11-11 (25.2 built):** about $0.006 per draft for a single-event entry: three live calls used 509 to 611 input tokens and 183 to 208 output tokens, thinking included. The estimate above assumed a prompt about three times larger than a single-event entry really is.

**Not decided:** which model fabricates least on this prompt. Nobody has measured it. If cost ever matters, run the same 20 to 30 real calendar days through each candidate and count flagged words before moving to a cheaper model.

### Verification for item 2

- Unit tests: a reply whose first block is a thinking block is parsed; `max_tokens` and `refusal` stop reasons leave the entry unchanged and record a failed generation; the setting overrides the default.
- `make test-live` produces a valid draft on `claude-opus-5-5` and the `llm_generations` row records that model and non-zero token counts.
- Read the recorded token counts and replace the cost estimate above with the measured figure.

---

## 3. AI suggestion box (C-5)

**Problem.** Regenerate picks its mode from "is there text" (`workers/llm.py:453`), not from who wrote the text. After the first AI draft every regeneration treats that draft as the parent's own account, keeps its facts, skips the word check and clears the "verify before publishing" warning. A fabrication in draft one becomes permanent and unmarked. Regenerate also overwrites the text field, as does a background redraft when a new event joins an entry.

**Decided by Andrew**

- When the entry already has text, the AI never overwrites it. Its version appears in a box under the text field.
- The suggestion text is selectable, so parts can be copied by hand.
- An Accept button replaces the text field's content with the suggestion.
- When the text field is empty, the first draft goes straight into the field, as today.

**Calls made here**

- *Who wrote it.* `body_source` gains the value `user`, set whenever a person saves a change to the body. Bodies marked `llm` or `fallback` are machine-only. Everything else (`user`, `llm_polished`, `llm_hybrid`) contains the parent's writing.
- *What Regenerate does.* Machine-only body: a fresh draft from the events with full checking. Body containing the parent's writing: the blend of their text and the events. Either way the result is a suggestion, not an overwrite.
- *Accept.* Takes the suggested title as well as the body. Accepting a fresh draft leaves the body machine-only; accepting a blend marks it `llm_hybrid`. **This refines what was said in conversation**, where "accepted" text was lumped in with typed text. Reason: after accepting a second take, a third Regenerate should still give a fresh take, not a rewording.
- *Dismiss.* A button discards the suggestion. A new suggestion replaces a pending one. Publishing discards any pending suggestion.
- *Warnings.* The suggestion shows its own "verify" words before Accept. Accepting carries them onto the entry. They stay until the word is no longer in the text or the entry is published.
- *Blend check.* Blend mode gains the word check it lacks today, flagging words found in neither the events nor the parent's text.
- *Background redrafts* (a new event joins the entry, or an event is rescheduled). Machine-only body: replaced in place, because the old draft is now out of date. Body containing the parent's writing: arrives as a suggestion.
- *Drafts only.* Published entries get no suggestions; unpublish first.

**Left to the builder**

- Where the pending suggestion is stored (columns on `entries` or its own table) and the exact endpoint shapes for accept and dismiss.
- The look of the box. A mock-up should be shown to Andrew before the page is built.

### Verification for item 3

- Integration tests: Regenerate on an untouched AI draft stores a suggestion and leaves the body byte-for-byte unchanged; the same after a typo fix runs the blend and still leaves the body unchanged; Accept replaces body and title and carries the flagged words; Dismiss clears it; publish clears it.
- The laundering case, as a test: draft one contains a flagged word that is in no event; Regenerate; the flagged word is still flagged on the entry, and the suggestion is checked independently.
- Unit test: blend mode flags a word that is in neither the events nor the parent's text, and does not flag a word the parent typed.
- Playwright: type text, Regenerate, see the box, select text inside it, Accept, see the field change.
- `make test-all` passes.

---

## 4. Self-updating NUC (P-2)

**Problem.** `scripts/nuc/40-update.sh` does not deploy new code. Compose defines the app services with `build:` and no `image:`, so its "pull" fetches nothing and the restart reuses the image from first deploy. Migrations run from that old image. `scripts/nuc/50-rollback.sh` pulls from `ghcr.io/andrewlass/...` while the repo owner is `bdsys`. The deploy workflow has failed in 0 seconds on every recent run, and its deploy job reaches the NUC by SSH from GitHub's servers.

**Decided by Andrew**

- A merge to `main` deploys itself.
- CI builds the images; the NUC does not.
- Nothing connects in to the house. The SSH deploy path goes.

**Design**

1. A merge to `main` runs the tests. If they pass, CI builds the `api` and `web` images and pushes them to GHCR tagged `sha-<short sha>`.
2. A systemd timer on the NUC runs every five minutes. It asks GitHub for the head commit of `main` (the NUC already has a deploy key for this) and compares it with `/opt/perfect-day/last-deployed-sha`.
3. If they differ and the image for that commit exists in GHCR, it runs the update script for that commit.
4. The update script checks out the commit, takes the encrypted database backup, pulls the images, runs migrations from the new image, restarts the app services and waits for `/readyz`.
5. If the health check fails, it restores the previous images, records the commit as failed and does not retry it. The next new commit is tried normally.

**Calls made here**

- Images are only pushed when the test workflow passes, so a red `main` never deploys.
- If the pre-migration backup fails, the update aborts.
- Rollback restores code, not the database. Migrations are forward-only, so each migration must keep working with the previous version of the code. This is a rule Andrew takes on when writing migrations.
- A pause file, `/etc/perfect-day/deploy-paused`, makes the timer do nothing.
- GHCR images are public, matching the already-public repo, so the NUC needs no registry token. They contain only code and the two `NEXT_PUBLIC_*` values, which are public by nature.
- Compose names the images (`ghcr.io/bdsys/perfect-day-api:${IMAGE_TAG}`, same for web) and keeps `build:` so local development still builds.
- `50-rollback.sh` takes the owner from one shared setting, corrected to `bdsys`.
- Removed: the `deploy-nuc` job, the secrets `NUC_HOST`, `NUC_USER` and `NUC_SSH_PRIVATE_KEY`, and the deploy user's authorised key on the NUC. Andrew removes the secrets and, if one exists, the SSH port forward on the FortiGate; neither can be seen from the repo.
- The external smoke test that ran from CI after the SSH step is dropped. The NUC's own health check replaces it.

**Things to be aware of**

- Whatever is merged to `main` ends up running as root on the NUC. Branch protection on `main` and the security of the GitHub account are therefore part of the NUC's security.
- A failed update is logged, and sent to Sentry if configured. Nothing else alerts. If more is wanted, that is a separate decision.
- The build half of the workflow has never been seen to run. The fix for its 0-second failure on `chore/code-review-2026-11` is unverified until pushed.

### Verification for item 4

- After pushing: the deploy workflow run is green and `sha-<short sha>` tags exist for both images in GHCR.
- `docker compose --profile nuc config` on the NUC shows the GHCR image names.
- Merge a trivial visible change; within ten minutes `last-deployed-sha` matches `main` and the change is live, with no one logged in.
- Merge a commit that fails `/readyz` on purpose (on a test branch pointed at by the timer, not real `main`); confirm the previous version is restored and the commit is recorded as failed and not retried.
- Create the pause file; merge; confirm nothing deploys. Remove it; confirm it deploys.
- Confirm a database backup file dated at the time of each update exists.
- From outside the house, confirm the SSH port is closed.

---

## 5. Old-format photos (S-1 follow-up)

**Problem.** Photos uploaded before the encryption fix use the old format, in which the full image and thumbnail share a key and nonce and the header is not tamper-protected. New uploads use the fixed format.

**Decided by Andrew**

- Only test uploads exist, on dev and on the NUC. No re-encryption script is needed.

**Calls made here**

- Delete the test photos, then remove the code that reads the old format (`MAGIC_V1` and the legacy branch in `apps/api/app/core/photo_crypto.py`) and its tests.
- Before removing the code, a small admin command reads the first four bytes of every stored photo object and reports how many are in the old format. The code is removed only when that count is zero on the NUC.
- Deleting the stored photos on the NUC is run by Andrew, not automatically.

### Verification for item 5

- The admin command reports zero old-format objects on the NUC and on dev.
- After removal, the photo unit tests pass and uploading, viewing and thumbnailing a new photo works end to end (`make test-all`).

---

## Backlog: automatic entries with no rule-writing

Not designed here. Recorded because it came out of the rules discussion and is the larger gap against the "magic" principle.

Today a parent who connects a calendar gets no entries until they open the rules screen and build a filter from AND/OR groups, fields, operators and typed text (`apps/web/src/components/RuleForm.tsx`). The diary wizard creates no rule. The original design had every calendar event become an entry with no rules; that was replaced on 2026-05-25, and no reason was written down.

The likely direction is that the app decides which events are about the child and the parent only corrects it. That needs its own design conversation before any build. The fixes in section 1 are deliberately small so they do not get in its way.

## Design documents this supersedes

Updated with pointers to this file; the full rewrites happen as each item is built.

- `design/06-scan-worker.md` — event-to-entry grouping.
- `design/04-llm-integration.md` — model choice and cost.
- `design/ci-cd.md` — single-host deployment and rollback.
