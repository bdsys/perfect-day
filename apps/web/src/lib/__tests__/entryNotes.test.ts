import { EVENT_REMOVED_NOTE, showEventRemovedNote } from '../entryNotes'

const base = {
  can_edit: true,
  status: 'draft' as const,
  body_source: 'llm' as const,
  creation_source: 'rule' as const,
  events: [] as unknown[],
}

describe('showEventRemovedNote', () => {
  it('shows for an untouched rule or calendar-pick draft whose events are all gone', () => {
    expect(showEventRemovedNote(base)).toBe(true)
    expect(showEventRemovedNote({ ...base, creation_source: 'calendar_pick' })).toBe(true)
    expect(showEventRemovedNote({ ...base, body_source: 'fallback' })).toBe(true)
  })

  it('hides once the parent has edited the text', () => {
    for (const body_source of ['user', 'llm_polished', 'llm_hybrid'] as const) {
      expect(showEventRemovedNote({ ...base, body_source })).toBe(false)
    }
  })

  it('hides while the entry still has an event', () => {
    expect(showEventRemovedNote({ ...base, events: [{ id: 'ev1' }] })).toBe(false)
  })

  it('hides for entries not built from a calendar event', () => {
    expect(showEventRemovedNote({ ...base, creation_source: 'manual' })).toBe(false)
    expect(showEventRemovedNote({ ...base, creation_source: 'legacy_auto' })).toBe(false)
  })

  it('hides on a published entry (the copy is about publishing)', () => {
    expect(showEventRemovedNote({ ...base, status: 'published' })).toBe(false)
  })

  it('hides for people who cannot edit the entry', () => {
    expect(showEventRemovedNote({ ...base, can_edit: false })).toBe(false)
  })
})

describe('EVENT_REMOVED_NOTE', () => {
  it('is the agreed copy', () => {
    expect(EVENT_REMOVED_NOTE).toBe(
      'The calendar event this entry was drafted from was moved or removed. Check the text before you publish.',
    )
  })
})
