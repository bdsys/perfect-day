'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Diary, type Entry } from '@/lib/api'
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
  return (
    <Link href={`/entries/${entry.id}`} style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}>
      <div className={`entry-card ${entry.status}`}>
        <div className="entry-date">{formatDate(entry.entry_date)}</div>
        <div className="entry-title">{entry.title ?? '(no title yet)'}</div>
        {preview && <div className="entry-preview">{preview}{entry.body_markdown && entry.body_markdown.length > 120 ? '…' : ''}</div>}
        <div style={{ marginTop: '0.5rem' }}>
          <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [scanning, setScanning] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

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
            <button className="btn btn-secondary" onClick={connectCalendar}>
              Connect Google Calendar
            </button>
            <button className="btn btn-primary" onClick={handleScan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Scan now'}
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
