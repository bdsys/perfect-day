'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type CalendarEventSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

// ── Date helpers ──────────────────────────────────────────────────────────────

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function startOfMonthGrid(monthStart: Date): Date {
  const d = new Date(monthStart)
  d.setDate(1 - monthStart.getDay()) // Go back to the previous Sunday
  return d
}

function buildMonthDays(monthStart: Date): Date[] {
  const start = startOfMonthGrid(monthStart)
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    return d
  })
}

// ── Event helpers ─────────────────────────────────────────────────────────────

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

// ── Component ─────────────────────────────────────────────────────────────────

export default function CalendarPickPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [events, setEvents] = useState<CalendarEventSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState<string | null>(null)

  const [cursorMonth, setCursorMonth] = useState<Date>(() => {
    const d = new Date()
    return new Date(d.getFullYear(), d.getMonth(), 1)
  })
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  const grouped = useMemo(() => groupByDate(events), [events])

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    setLoading(true)
    const gridDays = buildMonthDays(cursorMonth)
    const from = ymd(gridDays[0])
    const to = ymd(gridDays[gridDays.length - 1])
    api.calendarEvents.list(diaryId, { attached: false, from, to })
      .then(data => setEvents(data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load events'))
      .finally(() => setLoading(false))
  }, [user, diaryId, cursorMonth])

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
        const gridDays = buildMonthDays(cursorMonth)
        try {
          const refreshed = await api.calendarEvents.list(diaryId, {
            attached: false,
            from: ymd(gridDays[0]),
            to: ymd(gridDays[gridDays.length - 1]),
          })
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

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 960 }}>
        <h1 className="page-title">New entry from Google Calendar</h1>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Click a day to create a diary entry from its events. The LLM will generate a draft using the event details.
        </p>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {/* Month navigation toolbar */}
        <div className="cal-toolbar">
          <button className="btn btn-secondary" onClick={() => setCursorMonth(m => {
            const prev = new Date(m)
            prev.setMonth(prev.getMonth() - 1)
            return prev
          })}>←</button>
          <h2>{cursorMonth.toLocaleString('default', { month: 'long', year: 'numeric' })}</h2>
          <button className="btn btn-secondary" onClick={() => setCursorMonth(m => {
            const next = new Date(m)
            next.setMonth(next.getMonth() + 1)
            return next
          })}>→</button>
        </div>

        {/* Month grid */}
        <div className="cal-grid">
          {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(d => (
            <div key={d} className="cal-head">{d}</div>
          ))}
          {buildMonthDays(cursorMonth).map((d) => {
            const dayKey = ymd(d)
            const isOtherMonth = d.getMonth() !== cursorMonth.getMonth()
            return (
              <div
                key={dayKey}
                className={`cal-day${isOtherMonth ? ' is-other-month' : ''}`}
                onClick={() => setSelectedDay(dayKey)}
              >
                <span className="num">{d.getDate()}</span>
                {/* Event chips */}
                {(() => {
                  const dayEvs = grouped.get(dayKey) ?? []
                  return (
                    <>
                      {dayEvs.slice(0, 3).map((ev) => (
                        <button
                          key={ev.id}
                          className="cal-chip"
                          title={ev.summary || '(no title)'}
                          onClick={(e) => { e.stopPropagation(); setSelectedDay(dayKey) }}
                        >
                          {ev.summary || '(no title)'}
                        </button>
                      ))}
                      {dayEvs.length > 3 && (
                        <button className="cal-more" onClick={(e) => { e.stopPropagation(); setSelectedDay(dayKey) }}>
                          +{dayEvs.length - 3} more
                        </button>
                      )}
                    </>
                  )
                })()}
              </div>
            )
          })}
        </div>

        {/* Day-detail panel */}
        {selectedDay && (
          <div className="cal-panel">
            <div className="cal-panel-header">
              <h3>
                {new Date(selectedDay + 'T12:00:00').toLocaleDateString('default', {
                  weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'
                })}
              </h3>
              <button className="btn" onClick={() => setSelectedDay(null)}>✕</button>
            </div>
            {(grouped.get(selectedDay) ?? []).length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No events on this day.</p>
            ) : (
              (grouped.get(selectedDay) ?? []).map((ev) => (
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
                  </div>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </>
  )
}
