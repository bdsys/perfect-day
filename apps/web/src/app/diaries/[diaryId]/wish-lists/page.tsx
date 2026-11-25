'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import AppHeader from '@/components/AppHeader'
import { api, type Diary, type WishListSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatListDate } from '@/lib/date'
import { readOnlyBanner } from '@/lib/plan'

export default function WishListsPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [lists, setLists] = useState<WishListSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', list_date: '', pasted_list_url: '' })

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    ;(async () => {
      try {
        const [d, l] = await Promise.all([api.diaries.get(diaryId), api.wishLists.list(diaryId)])
        setDiary(d)
        setLists(l)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to load wish lists')
      } finally {
        setLoading(false)
      }
    })()
  }, [user, diaryId])

  async function create(ev: React.FormEvent) {
    ev.preventDefault()
    setError('')
    setBusy(true)
    try {
      const made = await api.wishLists.create(diaryId, {
        title: form.title.trim(),
        list_date: form.list_date,
        pasted_list_url: form.pasted_list_url.trim() || null,
      })
      router.push(`/diaries/${diaryId}/wish-lists/${made.id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
      setBusy(false)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  const manager = diary != null && diary.role !== 'viewer'
  const readOnly = manager && (diary?.plan?.read_only ?? false)

  return (
    <>
      <AppHeader />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <p>
          <Link href={`/diaries/${diaryId}`}>← {diary?.name ?? 'Diary'}</Link>
        </p>
        <h1 className="page-title">Wish lists</h1>
        {error && <p className="error-message" role="alert" style={{ marginBottom: '1rem' }}>{error}</p>}
        {readOnly && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>
            <p style={{ margin: 0 }}>{readOnlyBanner(diary?.plan?.plan_name ?? null)}</p>
            <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
          </div>
        )}

        {manager && !showForm && (
          <button type="button" className="btn btn-primary" style={{ marginBottom: '1rem' }} disabled={readOnly} onClick={() => setShowForm(true)}>
            New wish list
          </button>
        )}
        {manager && showForm && (
          <form onSubmit={create} aria-label="New wish list" style={{ marginBottom: '1.5rem' }}>
            <label>
              Title
              <input type="text" required maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </label>
            <label>
              Date
              <input type="date" required value={form.list_date} onChange={(e) => setForm({ ...form, list_date: e.target.value })} />
            </label>
            <label>
              Pasted list link (optional)
              <input type="url" maxLength={2048} value={form.pasted_list_url} onChange={(e) => setForm({ ...form, pasted_list_url: e.target.value })} />
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button type="submit" className="btn btn-primary" disabled={busy || !form.title.trim() || !form.list_date}>
                Make list
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
            </div>
          </form>
        )}

        {lists.length === 0 ? (
          <p>No wish lists yet.</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {lists.map((l) => (
              <li key={l.id} style={{ marginBottom: '0.5rem' }}>
                <Link href={`/diaries/${diaryId}/wish-lists/${l.id}`}>
                  {`${l.title} · ${formatListDate(l.list_date)}`}
                  {manager && ` · ${l.status === 'published' ? 'Published' : 'Draft'}`}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
