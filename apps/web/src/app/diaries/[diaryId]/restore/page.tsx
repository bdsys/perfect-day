'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Diary, type Entry, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function formatDate(d: string) {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function daysUntilDeletion(deletedAt: string): number {
  const deadline = new Date(deletedAt).getTime() + 30 * 86_400_000
  return Math.ceil((deadline - Date.now()) / 86_400_000)
}

export default function EntryRestorePage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tierLimitError, setTierLimitError] = useState<{ limit: number; current: number; source: string } | null>(null)
  const [restoring, setRestoring] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    Promise.all([api.diaries.get(diaryId), api.entries.listTrash(diaryId)])
      .then(([d, e]) => {
        setDiary(d)
        setEntries(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, diaryId])

  async function handleRestore(id: string) {
    setRestoring(id)
    setError('')
    setTierLimitError(null)
    try {
      await api.entries.restore(id)
      setEntries((prev) => prev.filter((e) => e.id !== id))
    } catch (e: unknown) {
      if (e instanceof ApiError && e.code === 'tier_limit' && e.details) {
        setTierLimitError({
          limit: typeof e.details.limit === 'number' ? e.details.limit : 0,
          current: typeof e.details.current === 'number' ? e.details.current : 0,
          source: typeof e.details.source === 'string' ? e.details.source : 'unknown',
        })
      } else {
        setError(e instanceof Error ? e.message : 'Restore failed')
      }
    } finally {
      setRestoring(null)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← {diary?.name ?? 'Diary'}</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <div className="page-header">
          <h1 className="page-title">Deleted entries</h1>
        </div>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {tierLimitError && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>
            You&apos;re at your free-tier limit ({tierLimitError.current}/{tierLimitError.limit} {tierLimitError.source === 'diary' ? 'diaries' : tierLimitError.source} entries).
            Free up a slot or{' '}
            <a href="/account/upgrade" style={{ textDecoration: 'underline' }}>Upgrade</a>
            {' '}to restore this one.
          </div>
        )}

        {entries.length === 0 ? (
          <div className="empty-state">
            <p>No deleted entries for this diary.</p>
          </div>
        ) : (
          <div>
            {entries.map((e) => {
              const days = e.deleted_at ? daysUntilDeletion(e.deleted_at) : null
              return (
                <div key={e.id} className="card" style={{ marginBottom: '0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '1rem' }}>{e.title ?? '(no title)'}</div>
                    <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                      {formatDate(e.entry_date)}
                    </div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.125rem' }}>
                      Deleted {new Date(e.deleted_at!).toLocaleDateString()}
                      {days !== null && days > 0
                        ? ` · Permanently deleted in ${days} day${days === 1 ? '' : 's'}`
                        : ' · Deletion pending'}
                    </div>
                  </div>
                  <button
                    className="btn btn-secondary"
                    onClick={() => handleRestore(e.id)}
                    disabled={restoring === e.id}
                  >
                    {restoring === e.id ? 'Restoring…' : 'Restore'}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </>
  )
}
