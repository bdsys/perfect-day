'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import AppHeader from '@/components/AppHeader'
import { useParams, useRouter } from 'next/navigation'
import { api, ApiError, type Diary, type Entry, type Integration, type ScanRun, type ScanConfig, type BackfillRun } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { StatusPanel } from '@/components/StatusPanel'
import { AiStatusBadge } from '@/components/AiStatusBadge'
import { usePolling } from '@/lib/usePolling'
import { formatDateRange } from '@/lib/date'
import { navigateTo } from '@/lib/navigate'
import { useResetOnBfcacheRestore } from '@/lib/useResetOnBfcacheRestore'
import PlanBadge from '@/components/PlanBadge'
import LimitNote from '@/components/LimitNote'
import { BACKFILL_LIMIT_NOTE, formatResetDate, limitMessage, readOnlyBanner } from '@/lib/plan'

// Must match the API's default page size for GET /v1/diaries/{id}/entries.
const ENTRIES_PAGE_SIZE = 20
const MAX_POLL_FAILURES = 5
const AI_STATUS_POLL_MS = 3000

function EntryCard({ entry, aiStalled }: { entry: Entry; aiStalled: boolean }) {
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
          <AiStatusBadge status={entry.ai_status ?? null} stalled={aiStalled} />
          {entry.events && entry.events.length > 0 && (
            <span style={{ fontSize: '0.75rem', color: '#888' }}>• {entry.events.length} event{entry.events.length !== 1 ? 's' : ''}</span>
          )}
        </div>
      </div>
    </Link>
  )
}

const CALENDAR_HINT = 'Connect Google Calendar to scan for new events, backfill past ones and make entries from your events.'
const OWNER_ONLY_HINT = 'Only the diary owner can scan for new events.'

// A scan's own count leaves out drafts made from the events it just fetched
// (they are evaluated after the run closes), so "0 new entries" would mislead.
function scanRunDetail(events: number, entries: number): string {
  const checked = `${events} event${events === 1 ? '' : 's'}`
  if (entries > 0) return `${checked} · ${entries} new entr${entries === 1 ? 'y' : 'ies'}`
  return `${checked} checked`
}

export default function DiaryDetailPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [hasMoreEntries, setHasMoreEntries] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [googleIntegration, setGoogleIntegration] = useState<Integration | null>(null)
  // The OWNER's calendar connection and whether this viewer may scan (owner only).
  const [scanAccess, setScanAccess] = useState<ScanConfig | null>(null)
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
  const [showNewEntryOptions, setShowNewEntryOptions] = useState(false)
  const [newEntryDate, setNewEntryDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [newEntryEndDate, setNewEntryEndDate] = useState('')
  const [newEntryTitle, setNewEntryTitle] = useState('')
  const [newEntryError, setNewEntryError] = useState('')
  const pollFailuresRef = useRef(0)
  const [aiFailures, setAiFailures] = useState(0)
  const aiInFlightRef = useRef(false)
  const entriesRef = useRef<Entry[]>([])
  entriesRef.current = entries
  const connectingRef = useRef(false)
  const [connecting, setConnecting] = useState(false)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    // Scans read the diary owner's calendar, so the owner's connection decides
    // what the page offers; the viewer's own connection only labels the header.
    Promise.all([api.diaries.getScanConfig(diaryId), api.integrations.list()])
      .then(([config, integrations]) => {
        setScanAccess(config)
        const google = integrations.find(
          (i: Integration) => i.provider === 'google' && !i.revoked && i.scopes_granted.includes('calendar.readonly'),
        )
        setGoogleIntegration(google ?? null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load the Google connection'))
  }, [user, diaryId])

  useEffect(() => {
    if (!user || !diaryId) return
    Promise.all([
      api.diaries.get(diaryId),
      api.entries.list(diaryId, statusFilter ? { status: statusFilter } : {}),
    ])
      .then(([d, e]) => {
        setDiary(d)
        showFirstPage(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, diaryId, statusFilter])

  // The API returns one page at a time; a full page means there may be more.
  function showFirstPage(page: Entry[]) {
    setEntries(page)
    setHasMoreEntries(page.length === ENTRIES_PAGE_SIZE)
  }

  async function handleLoadMore() {
    const last = entries[entries.length - 1]
    if (!last || loadingMore) return
    setLoadingMore(true)
    try {
      const older = await api.entries.list(diaryId, {
        ...(statusFilter ? { status: statusFilter } : {}),
        cursor: last.id,
      })
      setEntries((current) => [...current, ...older])
      setHasMoreEntries(older.length === ENTRIES_PAGE_SIZE)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load older entries')
    } finally {
      setLoadingMore(false)
    }
  }

  async function handleScan() {
    try {
      const result = await api.diaries.triggerScan(diaryId)
      if (result.queued || result.alreadyRunning) {
        setLatestRun(null)
        setPollingScan(true)
      }
    } catch (e: unknown) {
      setError(limitMessage(e, diary?.plan) ?? (e instanceof Error ? e.message : 'Scan failed'))
    }
  }

  function pastDaysCapped(days: number): number {
    const max = diary?.plan?.scan_past_days_max
    return max != null ? Math.min(days, max) : days
  }

  async function handleScanWithOptions() {
    setShowScanOptions(false)
    try {
      const result = await api.diaries.triggerScan(diaryId, { past_days: pastDaysCapped(pastDays), future_days: futureDays })
      if (result.queued || result.alreadyRunning) {
        setLatestRun(null)
        setPollingScan(true)
      }
    } catch (e: unknown) {
      setError(limitMessage(e, diary?.plan) ?? (e instanceof Error ? e.message : 'Scan failed'))
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
      setBackfillError(limitMessage(e, diary?.plan) ?? (e instanceof Error ? e.message : 'Backfill failed'))
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
        showFirstPage(updated)
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
          showFirstPage(updated)
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

  // Entries whose AI draft is queued or being written: poll until they finish.
  const aiPending = entries.some((e) => e.ai_status)
  const pollAiStatus = useCallback(async () => {
    if (aiInFlightRef.current) return
    aiInFlightRef.current = true
    try {
      const pending = await api.entries.aiStatus(diaryId)
      const finished = entriesRef.current.filter((e) => e.ai_status && !pending[e.id])
      // One bad re-fetch must not discard the rest of the poll.
      const results = await Promise.allSettled(finished.map((e) => api.entries.get(e.id)))
      const replacements = new Map<string, Entry>()
      const gone = new Set<string>()
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') replacements.set(finished[i].id, r.value)
        // Deleted elsewhere. Any other failure keeps the badge so the next poll retries.
        else if (r.reason instanceof ApiError && r.reason.status === 404) gone.add(finished[i].id)
      })
      setEntries((current) =>
        current
          .filter((e) => !gone.has(e.id))
          .map((e) => {
            if (replacements.has(e.id)) return replacements.get(e.id)!
            return e.ai_status && pending[e.id] ? { ...e, ai_status: pending[e.id] } : e
          }),
      )
      setAiFailures(0)
    } catch {
      setAiFailures((n) => n + 1)
    } finally {
      aiInFlightRef.current = false
    }
  }, [diaryId])

  usePolling(pollAiStatus, AI_STATUS_POLL_MS, aiPending && aiFailures < MAX_POLL_FAILURES)

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

  useResetOnBfcacheRestore(() => {
    connectingRef.current = false
    setConnecting(false)
  })

  async function connectCalendar() {
    if (connectingRef.current) return // two authorize calls would leave the wrong connect cookie
    connectingRef.current = true
    setConnecting(true)
    try {
      const { url } = await api.integrations.getGoogleAuthUrl('calendar')
      navigateTo(url)
    } catch (e: unknown) {
      connectingRef.current = false
      setConnecting(false)
      setError(limitMessage(e, diary?.plan) ?? (e instanceof Error ? e.message : 'Failed to get auth URL'))
    }
  }

  async function handleNewEntry() {
    setNewEntryError('')

    if (!newEntryDate) {
      setNewEntryError('Start date is required')
      return
    }
    if (newEntryEndDate && newEntryEndDate < newEntryDate) {
      setNewEntryError('End date must be on or after start date')
      return
    }

    const trimmedTitle = newEntryTitle.trim()

    setCreating(true)
    try {
      const entry = await api.entries.create(diaryId, {
        entry_date: newEntryDate,
        entry_end_date: newEntryEndDate || null,
        title: trimmedTitle || null,
      })
      router.push(`/entries/${entry.id}`)
    } catch (e: unknown) {
      setNewEntryError(limitMessage(e, diary?.plan) ?? (e instanceof Error ? e.message : 'Failed to create entry'))
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

  // Scan and Backfill read the owner's Google Calendar and only the owner may
  // start them (decision 2026-11-12). The hint says why they are switched off.
  const isOwner = scanAccess?.can_scan ?? false
  const calendar = scanAccess?.calendar
  const readOnly = diary?.plan?.read_only ?? false
  // Free scans look back 90 days and have no Backfill; the API refuses more, so
  // gray it out first. A viewer's plan is null: no plan info, the API still guards.
  const backfillOff = diary?.plan?.backfill === false
  const pastDaysMax = diary?.plan?.scan_past_days_max ?? null
  const calendarOff = readOnly || !isOwner || calendar !== 'connected'
  const scanHint = !scanAccess ? undefined
    : !isOwner ? OWNER_ONLY_HINT
    : calendar !== 'connected' ? CALENDAR_HINT
    : undefined

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <AppHeader />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        {readOnly && diary?.plan && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>
            <p style={{ margin: 0 }}>{readOnlyBanner(diary.plan.plan_name)}</p>
            <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
          </div>
        )}
        {diary?.plan && diary.plan.ai_drafts_limit != null && (
          <div style={{ marginBottom: '0.75rem' }}>
            <PlanBadge
              label="AI drafts"
              used={diary.plan.ai_drafts_limit - (diary.plan.ai_drafts_left ?? 0)}
              limit={diary.plan.ai_drafts_limit}
              resetsOn={diary.plan.resets_on}
              details={[
                {
                  label: 'AI drafts',
                  text: `${diary.plan.ai_drafts_left ?? 0} of ${diary.plan.ai_drafts_limit} left · resets ${formatResetDate(diary.plan.resets_on)}`,
                },
                {
                  label: 'Photos per entry',
                  text: diary.plan.photos_per_entry == null ? 'Unlimited' : String(diary.plan.photos_per_entry),
                },
              ]}
            />
          </div>
        )}
        <div className="page-header">
          <h1 className="page-title">{diary?.name ?? 'Diary'}</h1>
          <div className="page-actions">
            {readOnly ? (
              <button type="button" className="btn btn-secondary" disabled>Settings</button>
            ) : (
              <Link href={`/diaries/${diaryId}/settings`} className="btn btn-secondary">Settings</Link>
            )}
            {isOwner && calendar === 'connected' ? (
              <span className="btn btn-secondary" style={{ cursor: 'default', opacity: 0.7 }}>
                Connected: {googleIntegration?.google_name ?? 'Google account'}
                {googleIntegration?.google_email ? ` (${googleIntegration.google_email})` : ''} ✓
              </span>
            ) : isOwner && calendar === 'none' ? (
              <button className="btn btn-secondary" onClick={connectCalendar} disabled={connecting || readOnly}>
                Connect Google Calendar
              </button>
            ) : null}
            <div
              style={{ position: 'relative', display: 'inline-block' }}
              title={scanHint}
            >
              <div className="split-btn">
                <button
                  className="btn btn-primary"
                  onClick={handleScan}
                  disabled={pollingScan || calendarOff}
                  aria-describedby={scanHint ? 'calendar-hint' : undefined}
                >
                  {pollingScan ? 'Scanning…' : 'Scan now'}
                </button>
                <button
                  className="btn-gear"
                  aria-label="Scan options"
                  onClick={() => setShowScanOptions((v) => !v)}
                  disabled={pollingScan || calendarOff}
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
                      max={pastDaysMax ?? 3650}
                      value={pastDays}
                      onChange={(e) => setPastDays(Number(e.target.value))}
                    />
                  </label>
                  {pastDaysMax != null && <LimitNote>{BACKFILL_LIMIT_NOTE}</LimitNote>}
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
            <div
              style={{ position: 'relative', display: 'inline-block' }}
              title={scanHint}
            >
              <button
                className="btn btn-secondary"
                onClick={() => setShowBackfillOptions((v) => !v)}
                disabled={pollingBackfill || calendarOff || backfillOff}
                aria-describedby={backfillOff && isOwner ? 'backfill-limit-note' : scanHint ? 'calendar-hint' : undefined}
              >
                {pollingBackfill ? 'Backfilling…' : 'Backfill'}
              </button>
              {backfillOff && isOwner && (
                <>
                  <LimitNote id="backfill-limit-note">{BACKFILL_LIMIT_NOTE}</LimitNote>
                  <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
                </>
              )}
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
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setNewEntryError('')
                  setShowNewEntryOptions((v) => !v)
                }}
                disabled={creating || readOnly}
              >
                {creating ? 'Creating…' : 'New entry'}
              </button>
              {showNewEntryOptions && (
                <div className="popover" style={{ top: '100%', right: 0, marginTop: '0.4rem' }}>
                  <label>
                    Start date
                    <input
                      type="date"
                      value={newEntryDate}
                      onChange={(e) => setNewEntryDate(e.target.value)}
                    />
                  </label>
                  <label>
                    End date (optional)
                    <input
                      type="date"
                      value={newEntryEndDate}
                      onChange={(e) => setNewEntryEndDate(e.target.value)}
                    />
                  </label>
                  <label>
                    Title (optional)
                    <input
                      type="text"
                      value={newEntryTitle}
                      onChange={(e) => setNewEntryTitle(e.target.value)}
                    />
                  </label>
                  {newEntryError && (
                    <p className="error-message" style={{ marginBottom: '0.5rem' }}>{newEntryError}</p>
                  )}
                  <button
                    className="btn btn-primary"
                    style={{ width: '100%' }}
                    onClick={handleNewEntry}
                    disabled={creating || !newEntryDate}
                  >
                    {creating ? 'Creating…' : 'Create entry'}
                  </button>
                </div>
              )}
            </div>
            {/* The synced events are the owner's own calendar (S-11, decision 2026-11-12). */}
            {isOwner && (
              // A disabled button shows no tooltip, so the wrapper carries it (as for Scan now).
              <span style={{ display: 'inline-block' }} title={scanHint}>
                <button
                  className="btn btn-secondary"
                  onClick={() => router.push(`/diaries/${diaryId}/calendar-pick`)}
                  disabled={calendarOff}
                  aria-describedby={scanHint ? 'calendar-hint' : undefined}
                >
                  New entry from Google Calendar
                </button>
              </span>
            )}
            {/* Rules run over the owner's calendar, so they are the owner's (S-14). */}
            {isOwner && (
              <button
                className="btn btn-secondary"
                onClick={() => router.push(`/diaries/${diaryId}/rules`)}
                disabled={readOnly}
              >
                Auto-Creation Rules
              </button>
            )}
            <Link href="/photos" className="btn btn-secondary">
              Photos
            </Link>
            <Link href={`/diaries/${diaryId}/restore`} className="btn btn-secondary">
              Deleted entries
            </Link>
            <button className="btn btn-danger" onClick={handleDeleteDiary} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Delete diary'}
            </button>
          </div>
        </div>

        {calendar === 'lost' && (
          <div className="error-message" role="alert" style={{ marginBottom: '1rem' }}>
            {isOwner ? (
              <>
                <p style={{ margin: 0 }}>
                  Google Calendar is disconnected, so new events aren&apos;t being picked up.
                </p>
                <button className="btn btn-primary" style={{ marginTop: '0.5rem' }} onClick={connectCalendar} disabled={connecting}>
                  Reconnect Google Calendar
                </button>
              </>
            ) : (
              <p style={{ margin: 0 }}>
                The diary owner&apos;s Google Calendar is disconnected, so new events aren&apos;t
                being picked up. Ask them to reconnect it.
              </p>
            )}
          </div>
        )}
        {scanHint && (
          // The owner's lost-connection banner already explains; keep the hint for screen readers.
          <p
            id="calendar-hint"
            hidden={isOwner && calendar === 'lost'}
            style={{ marginBottom: '1rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}
          >
            {scanHint}
          </p>
        )}

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
                ? scanRunDetail(latestRun.events_calendar, latestRun.entries_created)
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
            // A completed run can still carry a note, e.g. events it couldn't save.
            note={
              latestBackfillRun.status === 'completed' && latestBackfillRun.error
                ? latestBackfillRun.error
                : undefined
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
            {isOwner ? (
              <p>
                No entries yet. Create one manually, pick from Google Calendar, or{' '}
                <Link href={`/diaries/${diaryId}/rules`}>set up auto-creation rules</Link>.
              </p>
            ) : (
              <p>No entries yet. Create one manually.</p>
            )}
          </div>
        ) : (
          entries.map((e) => <EntryCard key={e.id} entry={e} aiStalled={aiFailures >= MAX_POLL_FAILURES} />)
        )}

        {hasMoreEntries && (
          <div style={{ textAlign: 'center', marginTop: '1rem' }}>
            <button className="btn btn-secondary" onClick={handleLoadMore} disabled={loadingMore}>
              {loadingMore ? 'Loading…' : 'Load older entries'}
            </button>
          </div>
        )}
      </div>
    </>
  )
}
