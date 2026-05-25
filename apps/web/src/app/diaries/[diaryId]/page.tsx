'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Diary, type Entry, type Integration } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function formatDate(d: string) {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function EntryCard({ entry }: { entry: Entry }) {
  const preview = entry.body_markdown?.slice(0, 120) ?? ''
  const firstEventSummary = !entry.body_markdown && entry.events?.length > 0 ? entry.events[0].summary : null
  return (
    <Link href={`/entries/${entry.id}`} style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}>
      <div className={`entry-card ${entry.status}`}>
        <div className="entry-date">{formatDate(entry.entry_date)}</div>
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
  const [scanning, setScanning] = useState(false)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

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
    setScanning(true)
    try {
      await api.diaries.triggerScan(diaryId)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

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
            <button className="btn btn-primary" onClick={handleScan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Scan now'}
            </button>
            <button className="btn btn-primary" onClick={handleNewEntry} disabled={creating}>
              {creating ? 'Creating…' : 'New entry'}
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
            <p>No entries yet. Connect Google Calendar and trigger a scan.</p>
          </div>
        ) : (
          entries.map((e) => <EntryCard key={e.id} entry={e} />)
        )}
      </div>
    </>
  )
}
