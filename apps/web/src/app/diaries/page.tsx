'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { api, type Diary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function googleStatusMessage(
  status: string | null,
  missing: string | null,
): { text: string; isError: boolean } | null {
  if (!status) return null
  if (status === 'connected') return { text: 'Google Calendar connected successfully.', isError: false }
  if (status === 'partial' && missing === 'photos') return { text: 'Google Calendar connected. Photos access was not granted (not needed for Phase 1).', isError: false }
  if (status === 'partial' && missing === 'calendar') return { text: 'Calendar access was not granted. Please reconnect and allow Calendar.', isError: true }
  if (status === 'partial' && missing === 'all') return { text: 'No Google permissions were granted. Please try connecting again.', isError: true }
  if (status === 'denied') return { text: 'Google connection was cancelled or failed. Please try again.', isError: true }
  return null
}

function GoogleStatusBanner() {
  const searchParams = useSearchParams()
  const msg = googleStatusMessage(searchParams.get('google'), searchParams.get('missing'))
  if (!msg) return null
  return (
    <p className={msg.isError ? 'error-message' : 'success-message'} style={{ marginBottom: '1rem' }}>
      {msg.text}
    </p>
  )
}

function Nav({ onLogout }: { onLogout: () => void }) {
  return (
    <nav className="nav">
      <div className="nav-inner">
        <Link href="/diaries" className="nav-brand">Perfect Day</Link>
        <div className="nav-actions">
          <button className="btn btn-secondary" onClick={onLogout}>Sign out</button>
        </div>
      </div>
    </nav>
  )
}

export default function DiariesPage() {
  const { user, loading: authLoading, logout } = useAuth()
  const router = useRouter()
  const [diaries, setDiaries] = useState<Diary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone)

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace('/login')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    if (user) {
      api.diaries.list()
        .then(setDiaries)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false))
    }
  }, [user])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setCreating(true)
    try {
      const d = await api.diaries.create({ name: newName, timezone: newTz })
      setDiaries([...diaries, d])
      setNewName('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create diary')
    } finally {
      setCreating(false)
    }
  }

  async function handleLogout() {
    await logout()
    router.push('/login')
  }

  if (authLoading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <Nav onLogout={handleLogout} />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <div className="page-header">
          <h1 className="page-title">Your diaries</h1>
          <div className="page-actions">
            <Link href="/diaries/restore" className="btn btn-secondary">Deleted diaries</Link>
          </div>
        </div>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        <Suspense>
          <GoogleStatusBanner />
        </Suspense>

        {loading ? (
          <div className="loading">Loading…</div>
        ) : diaries.length === 0 ? (
          <div className="empty-state">
            <p>No diaries yet. Create your first one below.</p>
          </div>
        ) : (
          <div>
            {diaries.map((d) => (
              <Link key={d.id} href={`/diaries/${d.id}`} style={{ display: 'block', marginBottom: '0.75rem', color: 'inherit', textDecoration: 'none' }}>
                <div className="card" style={{ cursor: 'pointer', transition: 'border-color 0.15s' }}>
                  <div style={{ fontWeight: 600, fontSize: '1rem' }}>{d.name}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                    {d.timezone} · Scan every {d.scan_interval_minutes} min
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}

        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem' }}>Create a diary</h2>
          <form onSubmit={handleCreate}>
            <div className="form-field">
              <label className="form-label" htmlFor="diary-name">Name</label>
              <input
                id="diary-name"
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                placeholder="My diary"
              />
            </div>
            {/* TODO: surface timezone editing in a settings/edit-diary flow once PATCH /v1/diaries/{id} exists */}
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? 'Creating…' : 'Create diary'}
            </button>
          </form>
        </div>
      </div>
    </>
  )
}
