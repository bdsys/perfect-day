import type { Entry } from './api'

/**
 * The entry page's one AI button (tracker row 27). Its label says what the AI will do:
 * write a description from the events, or polish what the parent wrote. Only the label
 * follows the word count; the backend decides the AI's mode on its own
 * (`workers/llm.py`), and keeps any text a person wrote either way.
 */

/** From this many of the parent's words, the button offers to polish. */
export const POLISH_MIN_WORDS = 6

export const AI_BUTTON_LABELS = {
  create: { idle: 'Create event description with AI', running: 'Creating…' },
  polish: { idle: 'Polish event description with AI', running: 'Polishing…' },
} as const

/** Tooltip when there's nothing factual to write from: no events and almost no text. */
export const NEEDS_MORE_INPUT = 'Write a bit more or add events'

// Text only the machine wrote. Same set as MACHINE_ONLY_BODY_SOURCES in the API
// (`workers/event_changes.py`); llm_polished and llm_hybrid are rewrites of the parent's text.
const MACHINE_ONLY: ReadonlySet<Entry['body_source']> = new Set(['llm', 'fallback'])

/**
 * How many words of the body the parent wrote. The app knows only whether a person
 * touched the body, not which words are theirs, so a touched body counts in full and an
 * untouched one counts zero. A word holds at least one letter or digit, so Markdown
 * bullets, headings and lone emoji don't count.
 */
export function parentWordCount(entry: Pick<Entry, 'body_markdown' | 'body_source'>): number {
  if (MACHINE_ONLY.has(entry.body_source) || !entry.body_markdown) return 0
  return entry.body_markdown.split(/\s+/).filter((token) => /[\p{L}\p{N}]/u.test(token)).length
}

export function aiButtonState(
  entry: Pick<Entry, 'body_markdown' | 'body_source'> & { events: unknown[] },
): { mode: 'create' | 'polish'; needsMoreInput: boolean } {
  const polish = parentWordCount(entry) >= POLISH_MIN_WORDS
  return { mode: polish ? 'polish' : 'create', needsMoreInput: !polish && entry.events.length === 0 }
}
