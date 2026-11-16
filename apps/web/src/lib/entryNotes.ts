import type { Entry } from './api'

/** Shown on a draft whose calendar event went away but which was kept (e.g. for its photos). */
export const EVENT_REMOVED_NOTE =
  'The calendar event this entry was drafted from was moved or removed. Check the text before you publish.'

/**
 * True for a draft whose text is still the machine's, the entry was built from a calendar event,
 * and no event is left on it: the event it describes was moved or removed. Nothing is
 * stored for this; editing the text or attaching an event clears it on its own.
 */
export function showEventRemovedNote(
  entry: Pick<Entry, 'can_edit' | 'status' | 'body_source' | 'creation_source'> & { events: unknown[] },
): boolean {
  return (
    entry.can_edit &&
    entry.status === 'draft' &&
    (entry.body_source === 'llm' || entry.body_source === 'fallback') &&
    (entry.creation_source === 'rule' || entry.creation_source === 'calendar_pick') &&
    entry.events.length === 0
  )
}
