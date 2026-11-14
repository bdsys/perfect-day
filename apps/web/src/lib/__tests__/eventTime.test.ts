import { formatEventTime } from '../eventTime'
import type { EventItem } from '../api'

function event(start: string): EventItem {
  return {
    id: 'ev', source: 'google_calendar', occurred_at: start, summary: 'S', description: null,
    location: null, start: { dateTime: start }, end: {}, attendees: [], status: 'confirmed',
  }
}

describe('formatEventTime zone name (TR-26)', () => {
  afterEach(() => jest.useRealTimers())

  it.each([
    ['2026-07-04T10:00:00-07:00', 'PDT'],
    ['2026-01-04T10:00:00-08:00', 'PST'],
  ])('names the zone by the event date: %s is %s', (start, name) => {
    // "Today" is in the other season, so a name taken from now would be wrong.
    jest.useFakeTimers().setSystemTime(new Date(start.startsWith('2026-07') ? '2026-01-15T12:00:00Z' : '2026-07-15T12:00:00Z'))
    expect(formatEventTime(event(start), 'America/Los_Angeles')).toMatch(new RegExp(`${name}$`))
  })
})
