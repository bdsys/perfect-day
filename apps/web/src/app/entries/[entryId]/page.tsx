'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { api, type Entry, type EventItem } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { StatusPanel } from '@/components/StatusPanel'
import { usePolling } from '@/lib/usePolling'

function formatDate(d: string) {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function formatEventTime(event: EventItem): string {
  const start = event.start?.dateTime ?? event.start?.date ?? ''
  const end = event.end?.dateTime ?? event.end?.date ?? ''

  if (!start && !end) {
    return event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : 'Unknown time'
  }

  // All-day event
  if (event.start?.date && !event.start?.dateTime) {
    return 'All day'
  }

  const startTime = new Date(start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (!end || event.end?.date) return startTime

  const endTime = new Date(end).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return `${startTime}–${endTime}`
}

export default function EntryDetailPage() {
  return (
    <Suspense fallback={<div className="loading">Loading…</div>}>
      <EntryDetailPageInner />
    </Suspense>
  )
}

function EntryDetailPageInner() {
  const { entryId } = useParams<{ entryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const fromPick = searchParams.get('fromPick') === '1'

  const [entry, setEntry] = useState<Entry | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)

  const [publishing, setPublishing] = useState(false)
  const [pollingRegen, setPollingRegen] = useState(false)
  const [regenStartedAt, setRegenStartedAt] = useState<string | null>(null)
  const [regenStartTime, setRegenStartTime] = useState<string | null>(null)
  const [regenResult, setRegenResult] = useState<'success' | 'failed' | null>(null)
  const [regenSlow, setRegenSlow] = useState(false)
  const [deleting, setDeleting] = useState(false)

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
        // If we arrived from the picker, auto-start polling for LLM body
        if (fromPick && !e.body_markdown) {
          setRegenStartedAt(e.updated_at)
          setRegenStartTime(new Date().toISOString())
          setPollingRegen(true)
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [user, entryId, fromPick])

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
    try {
      setRegenStartedAt(entry.updated_at)
      setRegenStartTime(new Date().toISOString())
      setRegenResult(null)
      setRegenSlow(false)
      await api.entries.regenerate(entry.id)
      setPollingRegen(true)
      setError('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Regenerate failed')
    }
  }

  const pollRegen = useCallback(async () => {
    if (!entry) return
    try {
      const updated = await api.entries.get(entry.id)
      if (updated.updated_at !== regenStartedAt) {
        setEntry(updated)
        setPollingRegen(false)
        setRegenSlow(false)
        setRegenResult('success')
      }
    } catch {
    }
  }, [entry, regenStartedAt])

  usePolling(pollRegen, 2000, pollingRegen)

  useEffect(() => {
    if (!pollingRegen) return
    const slowTimer = setTimeout(() => setRegenSlow(true), 9 * 1000)
    const failTimer = setTimeout(() => {
      setPollingRegen(false)
      setRegenSlow(false)
      setRegenResult('failed')
    }, 30 * 1000)
    return () => {
      clearTimeout(slowTimer)
      clearTimeout(failTimer)
    }
  }, [pollingRegen])

  useEffect(() => {
    if (regenResult !== 'success') return
    const timer = setTimeout(() => {
      setRegenResult(null)
      setRegenStartTime(null)
    }, 8000)
    return () => clearTimeout(timer)
  }, [regenResult])

  async function handleDelete() {
    if (!entry) return
    if (!confirm('Delete this entry? You can restore it within 30 days.')) return
    setDeleting(true)
    try {
      await api.entries.delete(entry.id)
      router.push(`/diaries/${entry.diary_id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Delete failed')
      setDeleting(false)
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

            {entry.rule_matches && entry.rule_matches.length > 0 && (
              <div style={{ marginBottom: '0.75rem', fontSize: '0.85rem', color: '#555' }}>
                Captured by rule{entry.rule_matches.length !== 1 ? 's' : ''}:{' '}
                {entry.rule_matches.map((m, i) => (
                  <span key={m.rule_id}>
                    {i > 0 && ', '}
                    <a href={`/rules/${m.rule_id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                      {m.rule_name}
                    </a>
                  </span>
                ))}
              </div>
            )}

            {entry.body_source === 'fallback' && (
              <p style={{ fontStyle: 'italic', color: '#888', marginBottom: '1rem', fontSize: '0.85rem' }}>
                Generated from calendar events — LLM draft was not available. Edit or regenerate.
              </p>
            )}

            {entry.status === 'draft' && entry.flagged_tokens && entry.flagged_tokens.length > 0 && (
              <div style={{
                background: '#fffbeb',
                border: '1px solid #f59e0b',
                borderRadius: 6,
                padding: '0.75rem 1rem',
                marginBottom: '1rem',
                fontSize: '0.875rem',
                color: '#92400e',
              }}>
                <strong>⚠ Verify before publishing:</strong> This draft mentions{' '}
                <strong>{entry.flagged_tokens.join(', ')}</strong>. Make sure these match
                what actually happened before publishing.
              </div>
            )}

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

            {entry.events && entry.events.length > 0 && (
              <details open style={{ marginTop: '1.5rem' }}>
                <summary style={{ cursor: 'pointer', fontWeight: '600', fontSize: '0.9rem', color: '#555', marginBottom: '0.5rem' }}>
                  Source events ({entry.events.length})
                </summary>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {entry.events.map((event) => (
                    <li key={event.id} style={{ padding: '0.4rem 0', borderTop: '1px solid #eee' }}>
                      <div style={{ fontWeight: '500' }}>
                        {formatEventTime(event)} — {event.summary || '(no title)'}
                      </div>
                      {event.location && (
                        <div style={{ fontSize: '0.8rem', color: '#888' }}>{event.location}</div>
                      )}
                      {event.attendees && event.attendees.length > 0 && (
                        <div style={{ fontSize: '0.8rem', color: '#888' }}>
                          {event.attendees.length} attendee{event.attendees.length !== 1 ? 's' : ''}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {(pollingRegen || regenResult !== null) && (
              <StatusPanel
                state={pollingRegen ? 'running' : regenResult!}
                headline={
                  pollingRegen
                    ? (regenSlow
                        ? 'Still working — this is taking longer than expected…'
                        : (entry.body_markdown ? 'Regenerating draft…' : 'Generating draft…'))
                    : regenResult === 'success'
                      ? (entry.body_markdown ? 'Draft regenerated' : 'Draft generated')
                      : 'Generation is taking longer than expected — refresh the page to check'
                }
                startedAt={regenStartTime ?? undefined}
                onDismiss={() => { setRegenResult(null); setRegenStartTime(null) }}
              />
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
              <button className="btn btn-secondary" onClick={handleRegenerate} disabled={pollingRegen}>
                {pollingRegen
                  ? (entry.body_markdown ? 'Regenerating…' : 'Generating…')
                  : (entry.body_markdown ? 'Regenerate with AI' : 'Generate with AI')}
              </button>
              <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  )
}
