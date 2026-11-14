import type { EventItem } from '@/lib/api'

/** An event's time. With *timeZone* (the entry's own zone), times are shown there, with its name. */
export function formatEventTime(event: EventItem, timeZone?: string): string {
  const opts: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', ...(timeZone ? { timeZone } : {}) }
  const start = event.start?.dateTime ?? event.start?.date ?? ''
  // The zone's name depends on the date (PDT in July, PST in January), so use
  // the event's own date. Only an event with no start at all falls back to when it
  // happened, then to now.
  const zoneDate = new Date(event.start?.dateTime ?? event.occurred_at ?? Date.now())
  const zoneName = timeZone
    ? ` ${new Intl.DateTimeFormat(undefined, { timeZone, timeZoneName: 'short' }).formatToParts(zoneDate).find((p) => p.type === 'timeZoneName')?.value ?? timeZone}`
    : ''
  const end = event.end?.dateTime ?? event.end?.date ?? ''

  if (!start && !end) {
    return event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString(undefined, opts) + zoneName : 'Unknown time'
  }

  // All-day event
  if (event.start?.date && !event.start?.dateTime) {
    return 'All day'
  }

  const startTime = new Date(start).toLocaleTimeString(undefined, opts)
  if (!end || event.end?.date) return startTime + zoneName

  const endTime = new Date(end).toLocaleTimeString(undefined, opts)
  return `${startTime}–${endTime}${zoneName}`
}
