# AI Button Label (row 27) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The entry page's one AI button says "Create event description with AI" or "Polish event description with AI", depending on how many words the parent has written.

**Architecture:** A pure helper in `apps/web/src/lib/aiButton.ts` decides the mode (create or polish) and whether the button must be disabled for lack of input. `apps/web/src/app/entries/[entryId]/page.tsx` uses it in place of its nested label ternary. Web only: there are no backend changes, and what the AI does is unchanged.

**Tech Stack:** Next.js / React, TypeScript, Jest + Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-17-ai-button-label-design.md`

## Global Constraints

- **Labels, verbatim:**
  - idle: `Create event description with AI` and `Polish event description with AI`;
  - running: `Creating…` and `Polishing…`.
- **The parent's words:**
  - Count every word of `body_markdown` when `body_source` is `user`, `llm_polished` or `llm_hybrid`.
  - Count 0 when it's `llm` or `fallback`, or when the body is empty or null.
  - The title is never counted.
- **A word** is a whitespace-separated token that contains at least one letter or digit (Unicode). So Markdown bullets (`-`, `*`), `#` and lone emoji are not words.
- **Polish** at 6 words or more (`POLISH_MIN_WORDS = 6`). **Create** at 0–5.
- **Disabled for lack of input:**
  - when: no events and 5 words or fewer;
  - tooltip (`title`), verbatim: `Write a bit more or add events`.
  - This replaces today's empty-entry tooltip "Add events or write something first".
- **Unchanged:**
  - the disabled rules for read-only, plan limit (`aiOut`) and an already running draft;
  - `aria-describedby` for the plan-limit note;
  - the Regenerate status panel's headlines ("Generating draft…", "Regenerating draft…", …);
  - the backend.
- **Commands:**
  - run web commands from `apps/web`;
  - stage explicit paths only;
  - end each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A parent who fixed one typo in a long AI draft** (`body_source: 'user'`) gets "Polish…". That's by design (spec), and a page test pins it.
2. **A parent who cleared the text after editing** (`user`, empty body) gets "Create…". With no events the button is disabled with the tooltip. A helper test pins it.
3. **A body that's only Markdown symbols or emoji** counts 0. A helper test pins it.
4. **A published entry** follows the same rule. A page test with `status: 'published'` pins it.
5. **At the plan limit with no events and 3 words,** the button stays disabled and still points `aria-describedby` at the plan note. A page test pins it.

---

### Task 1: The helper

**Files:**
- Create: `apps/web/src/lib/aiButton.ts`
- Test: `apps/web/src/lib/__tests__/aiButton.test.ts`

**Interfaces:**
- Produces:
  - `export const POLISH_MIN_WORDS = 6`
  - `export const AI_BUTTON_LABELS = { create: { idle: 'Create event description with AI', running: 'Creating…' }, polish: { idle: 'Polish event description with AI', running: 'Polishing…' } } as const`
  - `export const NEEDS_MORE_INPUT = 'Write a bit more or add events'`
  - `export function parentWordCount(entry: Pick<Entry, 'body_markdown' | 'body_source'>): number`
  - `export function aiButtonState(entry: Pick<Entry, 'body_markdown' | 'body_source'> & { events: unknown[] }): { mode: 'create' | 'polish'; needsMoreInput: boolean }`

- [ ] **Step 1: Write the failing tests** in `aiButton.test.ts`:
  - `parentWordCount`:
    - `{body_source:'llm', body_markdown: <200 words>}` → 0
    - `fallback` → 0
    - `{user, null}` → 0
    - `{user, '   \n '}` → 0
    - `{user, 'Zoo with Grandma'}` → 3
    - `{user, 'one\ntwo\n\nthree'}` → 3
    - `{user, '- Zoo\n- Park'}` → 2
    - `{user, '# 🎉 * -'}` → 0
    - `{llm_polished, 'a b c d e f'}` → 6
    - `{llm_hybrid, 'a b c'}` → 3
  - `aiButtonState`:
    - `{user, 5 words, events:[{}]}` → `{mode:'create', needsMoreInput:false}`
    - `{user, 6 words, events:[]}` → `{mode:'polish', needsMoreInput:false}`
    - `{user, 5 words, events:[]}` → `{mode:'create', needsMoreInput:true}`
    - `{llm, 200 words, events:[]}` → `{mode:'create', needsMoreInput:true}`
    - `{user, '', events:[]}` → `{mode:'create', needsMoreInput:true}`

- [ ] **Step 2: Run** `npx jest src/lib/__tests__/aiButton.test.ts`. Expected: FAIL, "Cannot find module '../aiButton'".

- [ ] **Step 3: Implement** the Produces block. A word is a token of `body.split(/\s+/)` matching `/[\p{L}\p{N}]/u`.

- [ ] **Step 4: Run** the same command. Expected: PASS, all tests.

- [ ] **Step 5: Commit** `apps/web/src/lib/aiButton.ts` and `apps/web/src/lib/__tests__/aiButton.test.ts` with the message `feat(web): count the parent's words for the AI button (row 27)`.

### Task 2: The entry page uses it

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx:725-742` (the AI button)
- Modify: `apps/web/src/app/entries/[entryId]/__tests__/page.test.tsx`. Lines 119, 135, 182, 194, 203, 288, 387 and 406 match `/regenerate with ai/i`. Every fixture there is an `llm` body, so each becomes `/create event description with ai/i`.
- Modify: `apps/web/e2e/tier-limits.spec.ts:107,137`, `apps/web/e2e/suggestion-box.spec.ts:141,156`. Their regexes become `/(Create|Polish) event description with AI/`. In `suggestion-box.spec.ts`, match the running labels too where they matter: `/(Create|Polish) event description with AI|Creating…|Polishing…/`.
- Modify: `POC_PHASE2_TODO.md` row 27. Set the status to `**done** — PR #<n>, 2026-11-17; label only (Andrew); spec docs/superpowers/specs/2026-11-17-ai-button-label-design.md`.

**Interfaces:**
- Consumes: `aiButtonState`, `AI_BUTTON_LABELS`, `NEEDS_MORE_INPUT` from Task 1.

- [ ] **Step 1: Write the failing page tests** in a new block, `describe('entry page: AI button label (row 27)')`. Each one renders the page and finds the button by its name:
  - `llm` body with events → `Create event description with AI`, enabled.
  - `user` body `'one two three four five'` with events → Create.
  - `user` body of 6 words with events → `Polish event description with AI`.
  - `user` body of 200 words (one typo fixed) → Polish.
  - `llm_polished` body of 6 words, no events → Polish, enabled.
  - No events, `user` body `'Zoo with Grandma'`:
    - Create, disabled;
    - `title` is `Write a bit more or add events`.
  - No events, `llm` body: disabled, with the same `title`.
  - `status: 'published'`, `user` body of 6 words with events → Polish.
  - Plan limit:
    - `ai_drafts_left: 0`, no events, `user` body of 3 words;
    - the button is disabled;
    - `aria-describedby` points at the element with text `No AI drafts left this month. Resets Dec 1.`
  - Running:
    - mock `regenerate` to resolve with `ai_status: 'queued'`;
    - click Create; the button reads `Creating…`;
    - the same with a 6-word `user` body reads `Polishing…`.

- [ ] **Step 2: Run** `npx jest 'src/app/entries/\[entryId\]/__tests__/page.test.tsx'`. Expected: the new tests FAIL, because the labels still read Generate, Regenerate and Polish with AI.

- [ ] **Step 3: Implement.** Replace the button's label ternary with `aiButtonState(entry)` and `AI_BUTTON_LABELS[mode][pollingRegen ? 'running' : 'idle']`.
  - **`disabled`:** `pollingRegen || readOnly || aiOut || needsMoreInput`.
  - **`title`:** `needsMoreInput ? NEEDS_MORE_INPUT : undefined`.
  - **`aria-describedby`:** unchanged.
  - **Existing tests:** update the 8 regexes listed under Files.

- [ ] **Step 4: Run** the page test, then the web checks:
  - `npx jest`. Expected: all pass.
  - `npx tsc --noEmit`, then `npx eslint src --max-warnings 0`. Expected: clean.

- [ ] **Step 5: Update** the e2e regexes and tracker row 27. Commit the page, its test, the two e2e specs and `POC_PHASE2_TODO.md` with the message `feat(web): the AI button says Create or Polish from the parent's words (row 27)`.

### Final verification (after both tasks)

1. **Suites:** `make test-all` from the repo root. Expected: "All checks passed", with Playwright included.
2. **Dev-stack check:** `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`. Expected: smoke 17 passed, then the cleanup line.
3. **By hand on localhost** (`make up`, log in, open an entry):
   - An AI draft shows Create.
   - Edit and save 6 words: it shows Polish.
   - Edit and save 5 words: it shows Create.
   - A manual entry with no events and 3 words is disabled, with the tooltip.
