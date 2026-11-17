import { aiButtonState, parentWordCount, POLISH_MIN_WORDS, AI_BUTTON_LABELS, NEEDS_MORE_INPUT } from '../aiButton'
import type { Entry } from '../api'

type Src = Entry['body_source']
const body = (body_source: Src, body_markdown: string | null) => ({ body_source, body_markdown })
const words = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join(' ')

describe('parentWordCount', () => {
  it('counts nothing in untouched AI text', () => {
    expect(parentWordCount(body('llm', words(200)))).toBe(0)
  })
  it('counts nothing in the fallback text', () => {
    expect(parentWordCount(body('fallback', 'Zoo with Grandma and the lions'))).toBe(0)
  })
  it('counts nothing in an empty or missing body', () => {
    expect(parentWordCount(body('user', null))).toBe(0)
    expect(parentWordCount(body('user', '   \n '))).toBe(0)
  })
  it('counts the words a person wrote', () => {
    expect(parentWordCount(body('user', 'Zoo with Grandma'))).toBe(3)
  })
  it('splits on any whitespace, including blank lines', () => {
    expect(parentWordCount(body('user', 'one\ntwo\n\nthree'))).toBe(3)
  })
  it('does not count Markdown bullets, headings or lone emoji as words', () => {
    expect(parentWordCount(body('user', '- Zoo\n- Park'))).toBe(2)
    expect(parentWordCount(body('user', '# 🎉 * -'))).toBe(0)
  })
  it("counts AI rewrites of the parent's own text as theirs", () => {
    expect(parentWordCount(body('llm_polished', 'a b c d e f'))).toBe(6)
    expect(parentWordCount(body('llm_hybrid', 'a b c'))).toBe(3)
  })
})

describe('aiButtonState', () => {
  it('polishes from 6 words', () => {
    expect(POLISH_MIN_WORDS).toBe(6)
  })
  it('5 words with events is Create', () => {
    expect(aiButtonState({ ...body('user', words(5)), events: [{}] })).toEqual({ mode: 'create', needsMoreInput: false })
  })
  it('6 words with no events is Polish', () => {
    expect(aiButtonState({ ...body('user', words(6)), events: [] })).toEqual({ mode: 'polish', needsMoreInput: false })
  })
  it('5 words with no events needs more input', () => {
    expect(aiButtonState({ ...body('user', words(5)), events: [] })).toEqual({ mode: 'create', needsMoreInput: true })
  })
  it('untouched AI text with no events needs more input', () => {
    expect(aiButtonState({ ...body('llm', words(200)), events: [] })).toEqual({ mode: 'create', needsMoreInput: true })
  })
  it('text cleared after an edit, with no events, needs more input', () => {
    expect(aiButtonState({ ...body('user', ''), events: [] })).toEqual({ mode: 'create', needsMoreInput: true })
  })
})

describe('copy', () => {
  it('uses the approved wording', () => {
    expect(AI_BUTTON_LABELS).toEqual({
      create: { idle: 'Create event description with AI', running: 'Creating…' },
      polish: { idle: 'Polish event description with AI', running: 'Polishing…' },
    })
    expect(NEEDS_MORE_INPUT).toBe('Write a bit more or add events')
  })
})
