'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type CalendarEventSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function formatOccurredAt(event: CalendarEventSummary): string {
  const dtStr = event.start?.dateTime ?? event.start?.date ?? event.occurred_at
  if (!dtStr) return 'Unknown time'
  if (event.start?.date && !event.start?.dateTime) return 'All day'
  const dt = new Date(dtStr)
  const time = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const endDtStr = event.end?.dateTime
  if (!endDtStr) return time
  const endTime = new Date(endDtStr).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return `${time}–${endTime}`
}

function groupByDate(events: CalendarEventSummary[]): Map<string, CalendarEventSummary[]> {
  const map = new Map<string, CalendarEventSummary[]>()
  for (const ev of events) {
    const dateKey = ev.start?.date
      ?? (ev.start?.dateTime ? ev.start.dateTime.slice(0, 10) : null)
      ?? (ev.occurred_at ? ev.occurred_at.slice(0, 10) : 'unknown')
    const bucket = map.get(dateKey) ?? []
    bucket.push(ev)
    map.set(dateKey, bucket)
  }
  return new Map([...map.entries()].sort((a, b) => b[0].localeCompare(a[0])))
}

function formatDateHeading(dateStr: string): string {
  if (dateStr === 'unknown') return 'Unknown date'
  return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default function CalendarPickPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [events, setEvents] = useState<CalendarEventSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.calendarEvents.list(diaryId, { attached: false })
      .then(setEvents)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load events'))
      .finally(() => setLoading(false))
  }, [user, diaryId])

  async function handlePick(event: CalendarEventSummary) {
    setCreating(event.id)
    setError('')
    try {
      const entry = await api.calendarEvents.createFromEvent(diaryId, event.id)
      router.push(`/entries/${entry.id}?fromPick=1`)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to create entry'
      if (msg.includes('409') || msg.includes('event_already_attached')) {
        setError('That event was just claimed. Refreshing the list…')
        try {
          const refreshed = await api.calendarEvents.list(diaryId, { attached: false })
          setEvents(refreshed)
        } catch {
          // refresh failed; list may be stale but error message already shown
        }
      } else {
        setError(msg)
      }
      setCreating(null)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  const grouped = groupByDate(events)

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <h1 className="page-title">New entry from Google Calendar</h1>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Click an event to create a diary entry from it. The LLM will generate a draft using the event details.
        </p>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {events.length === 0 ? (
          <div className="empty-state">
            <p>No unattached calendar events found. Try running a scan first.</p>
          </div>
        ) : (
          [...grouped.entries()].map(([dateKey, dayEvents]) => (
            <div key={dateKey} style={{ marginBottom: '1.5rem' }}>
              <div style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                marginBottom: '0.5rem',
              }}>
                {formatDateHeading(dateKey)}
              </div>
              {dayEvents.map((ev) => (
                <button
                  key={ev.id}
                  className="entry-card"
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    cursor: creating !== null ? 'not-allowed' : 'pointer',
                    opacity: creating === ev.id ? 0.5 : 1,
                    border: 'none',
                    background: 'var(--card-bg)',
                    marginBottom: '0.5rem',
                  }}
                  disabled={creating !== null}
                  onClick={() => handlePick(ev)}
                >
                  <div className="entry-title">
                    {creating === ev.id ? 'Creating…' : (ev.summary || '(no title)')}
                  </div>
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    {formatOccurredAt(ev)}
                    {ev.location ? ` · ${ev.location}` : ''}
                    {ev.attendees?.length > 0 ? ` · ${ev.attendees.length} attendee${ev.attendees.length !== 1 ? 's' : ''}` : ''}
                  </div>
                  {ev.description && (
                    <div style={{ fontSize: '0.8rem', color: '#999', marginTop: '0.2rem' }}>
                      {ev.description.slice(0, 80)}{ev.description.length > 80 ? '…' : ''}
                    </div>
                  )}
                </button>
              ))}
            </div>
          ))
        )}
      </div>
    </>
  )
}
