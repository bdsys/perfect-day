'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Entry } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

function formatDate(d: string) {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default function EntryDetailPage() {
  const { entryId } = useParams<{ entryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [entry, setEntry] = useState<Entry | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)

  const [publishing, setPublishing] = useState(false)
  const [regenerating, setRegenerating] = useState(false)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !entryId) return
    api.entries.get(entryId)
      .then((e) => {
        setEntry(e)
        setEditTitle(e.title ?? '')
        setEditBody(e.body_markdown ?? '')
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, entryId])

  function startEdit() {
    if (!entry) return
    setEditTitle(entry.title ?? '')
    setEditBody(entry.body_markdown ?? '')
    setEditing(true)
  }

  async function handleSave() {
    if (!entry) return
    setSaving(true)
    try {
      const updated = await api.entries.patch(entry.id, {
        title: editTitle || null,
        body_markdown: editBody || null,
      })
      setEntry(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handlePublish() {
    if (!entry) return
    setPublishing(true)
    try {
      const updated = await api.entries.publish(entry.id)
      setEntry(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Publish failed')
    } finally {
      setPublishing(false)
    }
  }

  async function handleUnpublish() {
    if (!entry) return
    setPublishing(true)
    try {
      const updated = await api.entries.unpublish(entry.id)
      setEntry(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unpublish failed')
    } finally {
      setPublishing(false)
    }
  }

  async function handleRegenerate() {
    if (!entry) return
    setRegenerating(true)
    try {
      await api.entries.regenerate(entry.id)
      setError('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Regenerate failed')
    } finally {
      setRegenerating(false)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null
  if (!entry) return <div className="container" style={{ paddingTop: '1.5rem' }}><p className="error-message">{error || 'Entry not found.'}</p></div>

  const diaryHref = `/diaries/${entry.diary_id}`

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={diaryHref} className="nav-brand">← Diary</Link>
        </div>
      </nav>

      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
          <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
          <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{formatDate(entry.entry_date)}</span>
        </div>

        {editing ? (
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-title">Title</label>
              <input
                id="entry-title"
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="(no title)"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-body">Body</label>
              <textarea
                id="entry-body"
                value={editBody}
                onChange={(e) => setEditBody(e.target.value)}
                rows={20}
                style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.9rem', resize: 'vertical' }}
                placeholder="Entry body (Markdown)"
              />
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <h1 style={{ fontSize: '1.75rem', fontWeight: 700, margin: '0.5rem 0 1rem' }}>
              {entry.title ?? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>(no title yet)</span>}
            </h1>

            {entry.body_markdown ? (
              <div
                className="card"
                style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7, fontSize: '0.95rem' }}
              >
                {entry.body_markdown}
              </div>
            ) : (
              <div className="empty-state">
                <p>No content yet. Trigger a scan or regenerate to generate a draft.</p>
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1.25rem', flexWrap: 'wrap' }}>
              <button className="btn btn-secondary" onClick={startEdit}>
                Edit
              </button>
              {entry.status === 'draft' ? (
                <button className="btn btn-primary" onClick={handlePublish} disabled={publishing}>
                  {publishing ? 'Publishing…' : 'Publish'}
                </button>
              ) : (
                <button className="btn btn-secondary" onClick={handleUnpublish} disabled={publishing}>
                  {publishing ? 'Unpublishing…' : 'Unpublish'}
                </button>
              )}
              <button className="btn btn-secondary" onClick={handleRegenerate} disabled={regenerating}>
                {regenerating ? 'Queued…' : 'Regenerate'}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  )
}
