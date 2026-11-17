# One AI button whose label follows the parent's text — short design (tracker row 27)

Requested by Andrew on 2026-11-12. He approved the label-only approach on 2026-11-17.

## Why

Today the entry page's AI button reads "Generate with AI", "Regenerate with AI"
or "Polish with AI". The label depends on whether the entry has events and any
text (`apps/web/src/app/entries/[entryId]/page.tsx:733-741`). It does not depend
on what the AI will actually do, which is decided by who wrote the text
(`apps/api/app/workers/llm.py:594-606`):

- If a person has touched the text, the AI keeps their words. It blends them
  with the events ("hybrid"), or polishes them when there are no events.
- If the text is untouched AI output or the fallback, the AI writes a fresh
  draft from the events.

Row 27 makes the label say which of the two the parent will get.

## Approaches

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | **Label only.** The 5-word line changes the button's label. The AI's behaviour stays as it is today, where any text a person wrote is kept and used. | Web only and small. A parent who wrote "Zoo with Grandma" and presses Create gets a draft that uses those words, which is what they'd want. | **Picked** (Andrew, 2026-11-17) |
| B | **Label and behaviour.** With 5 words or fewer, the AI ignores the parent's text and drafts from the events alone; its draft lands as a suggestion. | Needs a backend change, and drops the parent's few words of facts. | Rejected |

## The design

**The parent's word count.**

- If a person wrote or touched the body, count every word in the body.
- Otherwise count zero. The body is untouched machine text when `body_source` is `llm` or `fallback`, or when there is no body.
- The words are the body split on whitespace. The title is not counted.

This is my call. The app records whether a person touched the body as a whole, not which words are theirs: one edited word marks the whole body as `user` (`apps/api/app/routers/v1/entries.py:567`). `llm_polished` and `llm_hybrid` count as the parent's words. They are AI rewrites of the parent's own text, and the backend already treats them as the parent's (`MACHINE_ONLY_BODY_SOURCES` in `workers/event_changes.py:22`).

**The label (Andrew's wording).**

| The parent's words | Idle label | While running |
|---|---|---|
| 0–5 | Create event description with AI | Creating… |
| 6 or more | Polish event description with AI | Polishing… |

The "Regenerate with AI" label goes away. An untouched AI draft reads "Create event description with AI", and pressing it gives a fresh draft, as it does today. My call: follow row 27's wording as written, even though "Create" sits beside text that's already there.

**When the button is disabled.**

- **New:** with no events and 5 words or fewer, the button is disabled. Its tooltip reads "Write a bit more or add events".
  - Today it's enabled as soon as there's any text.
  - With no events and almost no text, the AI has nothing factual to write from (the facts-only rule).
  - My call.
- **Unchanged:**
  - read-only diaries;
  - the plan limit, with its note;
  - while a draft is already running.

**Out of scope:**

- The backend.
- The Regenerate status panel's headline: "Generating draft…", "Regenerating draft…", "Suggestion ready below your text".
- The suggestion box.
- Published entries. They use the same rule, and their result still lands as a suggestion.

## Left to the builder

Where the word-count helper lives (a small pure function next to the page or in `lib/`), and its name.

## Verification

- **Jest tests on the entry page:**
  - 0 words with events shows "Create…".
  - An untouched `llm` draft of 200 words shows "Create…".
  - A `user` body of exactly 5 words shows "Create…".
  - A body of 6 words shows "Polish…".
  - `llm_polished` with 6 or more words shows "Polish…".
  - No events with 5 or fewer words: disabled, with the tooltip.
  - No events with 6 or more words: enabled, "Polish…".
  - The running labels.
- **Unit tests for the word-count helper:** whitespace only, newlines, and Markdown bullets.
- **Playwright:** update the label regexes in `e2e/tier-limits.spec.ts:107,137` and `e2e/suggestion-box.spec.ts:141,156` to the new names.
- **Suites:** `make test-all`, then the dev-stack check.
- **By hand on localhost:**
  - An AI-drafted entry shows Create.
  - Edit and save 6 words: it shows Polish.
  - Edit and save 5 words: it shows Create.
  - The button sits in view mode, so the label follows the saved text.
  - A manual entry with no events and 3 words is disabled.
