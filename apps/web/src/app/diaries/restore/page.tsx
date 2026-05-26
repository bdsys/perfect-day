'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api, type Diary, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function daysRemaining(hardDeleteAfter: string): number {
  return Math.ceil((new Date(hardDeleteAfter).getTime() - Date.now()) / 86_400_000)
}

export default function DiaryRestorePage() {
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diaries, setDiaries] = useState<Diary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tierLimitError, setTierLimitError] = useState<{ limit: number; current: number; source: string } | null>(null)
  const [restoring, setRestoring] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (user) {
      api.diaries.listTrash()
        .then(setDiaries)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false))
    }
  }, [user])

  async function handleRestore(id: string) {
    setRestoring(id)
    setError('')
    setTierLimitError(null)
    try {
      await api.diaries.restore(id)
      setDiaries((prev) => prev.filter((d) => d.id !== id))
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

  if (authLoading) return <div className="loading">Loading…</div>
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
          <h1 className="page-title">Deleted diaries</h1>
        </div>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {tierLimitError && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>
            You&apos;re at your free-tier limit ({tierLimitError.current}/{tierLimitError.limit} {tierLimitError.source === 'diary' ? 'diaries' : 'entries'}).
            Free up a slot or{' '}
            <a href="/account/upgrade" style={{ textDecoration: 'underline' }}>Upgrade</a>
            {' '}to restore this one.
          </div>
        )}

        {loading ? (
          <div className="loading">Loading…</div>
        ) : diaries.length === 0 ? (
          <div className="empty-state">
            <p>No deleted diaries. Deleted diaries appear here for 30 days.</p>
          </div>
        ) : (
          <div>
            {diaries.map((d) => {
              const days = d.hard_delete_after ? daysRemaining(d.hard_delete_after) : null
              return (
                <div key={d.id} className="card" style={{ marginBottom: '0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '1rem' }}>{d.name}</div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                      Deleted {new Date(d.deleted_at!).toLocaleDateString()}
                      {days !== null && ` · Permanently deleted in ${days} day${days === 1 ? '' : 's'}`}
                    </div>
                  </div>
                  <button
                    className="btn btn-secondary"
                    onClick={() => handleRestore(d.id)}
                    disabled={restoring === d.id}
                  >
                    {restoring === d.id ? 'Restoring…' : 'Restore'}
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
