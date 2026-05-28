'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Diary, type Entry, type Integration, type ScanRun, type BackfillRun } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { StatusPanel } from '@/components/StatusPanel'
import { usePolling } from '@/lib/usePolling'
import { formatDateRange } from '@/lib/date'

function EntryCard({ entry }: { entry: Entry }) {
  const preview = entry.body_markdown?.slice(0, 120) ?? ''
  const firstEventSummary = !entry.body_markdown && entry.events?.length > 0 ? entry.events[0].summary : null
  return (
    <Link href={`/entries/${entry.id}`} style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}>
      <div className={`entry-card ${entry.status}`}>
        <div className="entry-date">{formatDateRange(entry.entry_date, entry.entry_end_date)}</div>
        <div className="entry-title">{entry.title ?? '(no title yet)'}</div>
        {firstEventSummary ? (
          <div style={{ fontStyle: 'italic', color: '#888', fontSize: '0.85rem', marginTop: '0.25rem' }}>
            {firstEventSummary}{entry.events.length > 1 ? `, +${entry.events.length - 1} more` : ''}
          </div>
        ) : (
          preview && <div className="entry-preview">{preview}{entry.body_markdown && entry.body_markdown.length > 120 ? '…' : ''}</div>
        )}
        <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
          {entry.events && entry.events.length > 0 && (
            <span style={{ fontSize: '0.75rem', color: '#888' }}>• {entry.events.length} event{entry.events.length !== 1 ? 's' : ''}</span>
          )}
        </div>
      </div>
    </Link>
  )
}

export default function DiaryDetailPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [googleIntegration, setGoogleIntegration] = useState<Integration | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pollingScan, setPollingScan] = useState(false)
  const [latestRun, setLatestRun] = useState<ScanRun | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [showScanOptions, setShowScanOptions] = useState(false)
  const [pastDays, setPastDays] = useState(90)
  const [futureDays, setFutureDays] = useState(90)
  const [showBackfillOptions, setShowBackfillOptions] = useState(false)
  const [backfillFrom, setBackfillFrom] = useState('')
  const [backfillTo, setBackfillTo] = useState('')
  const [pollingBackfill, setPollingBackfill] = useState(false)
  const [latestBackfillRun, setLatestBackfillRun] = useState<BackfillRun | null>(null)
  const [backfillError, setBackfillError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const pollFailuresRef = useRef(0)
  const MAX_POLL_FAILURES = 5

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.integrations.list()
      .then((integrations) => {
        const google = integrations.find(
          (i: Integration) => i.provider === 'google' && !i.revoked && i.scopes_granted.includes('calendar.readonly'),
        )
        setGoogleIntegration(google ?? null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load integrations'))
  }, [user, diaryId])

  useEffect(() => {
    if (!user || !diaryId) return
    Promise.all([
      api.diaries.get(diaryId),
      api.entries.list(diaryId, statusFilter ? { status: statusFilter } : {}),
    ])
      .then(([d, e]) => {
        setDiary(d)
        setEntries(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, diaryId, statusFilter])

  async function handleScan() {
    try {
      const result = await api.diaries.triggerScan(diaryId)
      if (result.queued || result.alreadyRunning) {
        setLatestRun(null)
        setPollingScan(true)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    }
  }

  async function handleScanWithOptions() {
    setShowScanOptions(false)
    try {
      const result = await api.diaries.triggerScan(diaryId, { past_days: pastDays, future_days: futureDays })
      if (result.queued || result.alreadyRunning) {
        setLatestRun(null)
        setPollingScan(true)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    }
  }

  async function handleBackfill() {
    setBackfillError('')
    if (!backfillFrom || !backfillTo) {
      setBackfillError('Both dates are required.')
      return
    }
    if (backfillFrom > backfillTo) {
      setBackfillError('Start date must be before end date.')
      return
    }
    try {
      const result = await api.diaries.triggerBackfill(diaryId, backfillFrom, backfillTo)
      if ('alreadyRunning' in result) {
        setBackfillError('A scan or backfill is already running. Try again in a minute.')
        return
      }
      setShowBackfillOptions(false)
      setLatestBackfillRun(result)
      pollFailuresRef.current = 0
      setPollingBackfill(true)
    } catch (e: unknown) {
      setBackfillError(e instanceof Error ? e.message : 'Backfill failed')
    }
  }

  async function handleCancelBackfill() {
    if (!latestBackfillRun) return
    setCancelling(true)
    try {
      const run = await api.diaries.cancelBackfillRun(diaryId, latestBackfillRun.id)
      setLatestBackfillRun(run)
      setPollingBackfill(false)
    } catch (e: unknown) {
      setBackfillError(e instanceof Error ? e.message : 'Cancel failed')
    } finally {
      setCancelling(false)
    }
  }

  const pollScan = useCallback(async () => {
    try {
      const runs = await api.diaries.listScanRuns(diaryId)
      if (runs.length === 0) return
      const run = runs[0]
      setLatestRun(run)
      if (run.status === 'success' || run.status === 'partial' || run.status === 'failed') {
        setPollingScan(false)
        const updated = await api.entries.list(diaryId, statusFilter ? { status: statusFilter } : {})
        setEntries(updated)
      }
    } catch {
    }
  }, [diaryId, statusFilter])

  usePolling(pollScan, 2000, pollingScan)

  useEffect(() => {
    if (!pollingScan) return
    const timer = setTimeout(() => {
      setPollingScan(false)
      setLatestRun(prev => prev?.status === 'running' ? { ...prev, status: 'failed' } : prev)
    }, 5 * 60 * 1000)
    return () => clearTimeout(timer)
  }, [pollingScan])

  const pollBackfill = useCallback(async () => {
    if (!latestBackfillRun) return
    try {
      const run = await api.diaries.getBackfillRun(diaryId, latestBackfillRun.id)
      pollFailuresRef.current = 0
      setLatestBackfillRun(run)
      if (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') {
        setPollingBackfill(false)
        if (run.status === 'completed') {
          const updated = await api.entries.list(diaryId, statusFilter ? { status: statusFilter } : {})
          setEntries(updated)
        }
      }
    } catch {
      pollFailuresRef.current += 1
      if (pollFailuresRef.current >= MAX_POLL_FAILURES) {
        setPollingBackfill(false)
        setLatestBackfillRun(prev =>
          prev && (prev.status === 'running' || prev.status === 'pending')
            ? { ...prev, status: 'failed', error: 'Connection lost — refresh the page to check status' }
            : prev,
        )
      }
    }
  }, [diaryId, latestBackfillRun, statusFilter])

  usePolling(pollBackfill, 3000, pollingBackfill)

  useEffect(() => {
    if (!pollingBackfill) return
    const timer = setTimeout(() => {
      setPollingBackfill(false)
      setLatestBackfillRun(prev =>
        prev?.status === 'running' || prev?.status === 'pending'
          ? { ...prev, status: 'failed', error: 'Polling timed out — refresh the page to check status' }
          : prev,
      )
    }, 60 * 60 * 1000)
    return () => clearTimeout(timer)
  }, [pollingBackfill])

  async function connectCalendar() {
    try {
      const { url } = await api.integrations.getGoogleAuthUrl('calendar')
      window.location.href = url
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to get auth URL')
    }
  }

  async function handleNewEntry() {
    setCreating(true)
    try {
      const today = new Date().toISOString().slice(0, 10)
      const entry = await api.entries.create(diaryId, { entry_date: today })
      router.push(`/entries/${entry.id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create entry')
      setCreating(false)
    }
  }

  async function handleDeleteDiary() {
    if (!confirm(`Delete "${diary?.name ?? 'this diary'}"? You can restore it within 30 days.`)) return
    setDeleting(true)
    try {
      await api.diaries.delete(diaryId)
      router.push('/diaries')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Delete failed')
      setDeleting(false)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href="/diaries" className="nav-brand">← Perfect Day</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <div className="page-header">
          <h1 className="page-title">{diary?.name ?? 'Diary'}</h1>
          <div className="page-actions">
            {googleIntegration ? (
              <span className="btn btn-secondary" style={{ cursor: 'default', opacity: 0.7 }}>
                Connected: {googleIntegration.google_name ?? 'Google account'}
                {googleIntegration.google_email ? ` (${googleIntegration.google_email})` : ''} ✓
              </span>
            ) : (
              <button className="btn btn-secondary" onClick={connectCalendar}>
                Connect Google Calendar
              </button>
            )}
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <div className="split-btn">
                <button className="btn btn-primary" onClick={handleScan} disabled={pollingScan}>
                  {pollingScan ? 'Scanning…' : 'Scan now'}
                </button>
                <button
                  className="btn-gear"
                  aria-label="Scan options"
                  onClick={() => setShowScanOptions((v) => !v)}
                  disabled={pollingScan}
                >
                  ⚙
                </button>
              </div>
              {showScanOptions && (
                <div className="popover" style={{ top: '100%', right: 0, marginTop: '0.4rem' }}>
                  <label>
                    Past days
                    <input
                      type="number"
                      min={1}
                      max={3650}
                      value={pastDays}
                      onChange={(e) => setPastDays(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Future days
                    <input
                      type="number"
                      min={1}
                      max={3650}
                      value={futureDays}
                      onChange={(e) => setFutureDays(Number(e.target.value))}
                    />
                  </label>
                  <button className="btn btn-primary" style={{ width: '100%' }} onClick={handleScanWithOptions} disabled={pollingScan}>
                    Run scan
                  </button>
                </div>
              )}
            </div>
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <button
                className="btn btn-secondary"
                onClick={() => setShowBackfillOptions((v) => !v)}
                disabled={pollingBackfill}
              >
                {pollingBackfill ? 'Backfilling…' : 'Backfill'}
              </button>
              {showBackfillOptions && (
                <div className="popover" style={{ top: '100%', right: 0, marginTop: '0.4rem' }}>
                  <label>
                    From date
                    <input
                      type="date"
                      value={backfillFrom}
                      onChange={(e) => setBackfillFrom(e.target.value)}
                    />
                  </label>
                  <label>
                    To date
                    <input
                      type="date"
                      value={backfillTo}
                      onChange={(e) => setBackfillTo(e.target.value)}
                    />
                  </label>
                  {backfillError && (
                    <p className="error-message" style={{ marginBottom: '0.5rem' }}>{backfillError}</p>
                  )}
                  <button
                    className="btn btn-primary"
                    style={{ width: '100%' }}
                    onClick={handleBackfill}
                    disabled={pollingBackfill}
                  >
                    Start backfill
                  </button>
                </div>
              )}
            </div>
            <button className="btn btn-primary" onClick={handleNewEntry} disabled={creating}>
              {creating ? 'Creating…' : 'New entry'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => router.push(`/diaries/${diaryId}/calendar-pick`)}
            >
              New entry from Google Calendar
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => router.push(`/diaries/${diaryId}/rules`)}
            >
              Auto-Creation Rules
            </button>
            <Link href={`/diaries/${diaryId}/restore`} className="btn btn-secondary">
              Deleted entries
            </Link>
            <button className="btn btn-danger" onClick={handleDeleteDiary} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Delete diary'}
            </button>
          </div>
        </div>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {latestRun && (
          <StatusPanel
            state={latestRun.status}
            headline={
              latestRun.status === 'running' ? 'Scanning…' :
              latestRun.status === 'success' ? `Scan complete` :
              latestRun.status === 'partial' ? 'Scan completed with errors' :
              'Scan failed'
            }
            detail={
              latestRun.status !== 'running' && latestRun.completed_at
                ? `${latestRun.events_calendar} events · ${latestRun.entries_created} new entries`
                : undefined
            }
            errors={latestRun.errors?.map(e => e.message)}
            startedAt={latestRun.started_at}
            onDismiss={() => setLatestRun(null)}
          />
        )}

        {latestBackfillRun && (
          <StatusPanel
            state={
              // 'pending' and 'running' both show the spinner — no separate pending state in StatusPanel.
              latestBackfillRun.status === 'completed' ? 'success' :
              latestBackfillRun.status === 'failed' ? 'failed' :
              latestBackfillRun.status === 'cancelled' ? 'cancelled' :
              'running'
            }
            headline={
              latestBackfillRun.status === 'running' || latestBackfillRun.status === 'pending'
                ? 'Backfilling…' :
              latestBackfillRun.status === 'completed' ? 'Backfill complete' :
              latestBackfillRun.status === 'cancelled' ? 'Backfill cancelled' :
              'Backfill failed'
            }
            detail={
              latestBackfillRun.status === 'completed'
                ? `${latestBackfillRun.events_ingested} events · ${latestBackfillRun.entries_created} new entries`
                : latestBackfillRun.error ?? undefined
            }
            startedAt={latestBackfillRun.started_at ?? undefined}
            onDismiss={() => setLatestBackfillRun(null)}
          />
        )}
        {latestBackfillRun &&
          (latestBackfillRun.status === 'pending' || latestBackfillRun.status === 'running') && (
          <button
            className="btn btn-secondary"
            style={{ marginBottom: '0.75rem' }}
            onClick={handleCancelBackfill}
            disabled={cancelling}
          >
            {cancelling ? 'Cancelling…' : 'Cancel backfill'}
          </button>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
          {['', 'draft', 'published'].map((f) => (
            <button
              key={f}
              className={`btn ${statusFilter === f ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setStatusFilter(f)}
            >
              {f === '' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>

        {entries.length === 0 ? (
          <div className="empty-state">
            <p>
              No entries yet. Create one manually, pick from Google Calendar, or{' '}
              <Link href={`/diaries/${diaryId}/rules`}>set up auto-creation rules</Link>.
            </p>
          </div>
        ) : (
          entries.map((e) => <EntryCard key={e.id} entry={e} />)
        )}
      </div>
    </>
  )
}
