'use client'

import { Suspense, useCallback, useEffect, useEffectEvent, useRef, useState, type CSSProperties } from 'react'
import AppHeader from '@/components/AppHeader'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { api, type Entry, type OwnerPlan, type TemperatureUnit } from '@/lib/api'
import { formatEventTime } from '@/lib/eventTime'
import { PhotoThumbnail } from '@/components/PhotoThumbnail'
import { PhotoLightbox } from '@/components/PhotoLightbox'
import { AddPhotos } from '@/components/AddPhotos'
import { useAuth } from '@/lib/auth-context'
import { StatusPanel } from '@/components/StatusPanel'
import { usePolling } from '@/lib/usePolling'
import { useLatest } from '@/lib/useLatest'
import { formatDateRange } from '@/lib/date'
import { WeatherBadge } from '@/components/WeatherBadge'
import { SuggestionBox } from '@/components/SuggestionBox'
import { EntryPlace } from '@/components/EntryPlace'
import { GooglePhotosPick } from '@/components/GooglePhotosPick'
import { PhotoImportNotice } from '@/components/PhotoImportNotice'
import { googleStatusMessage } from '@/lib/googleStatus'
import PlanBadge from '@/components/PlanBadge'
import LimitNote from '@/components/LimitNote'
import { aiEntryNote, aiLimitNote, formatResetDate, limitMessage, readOnlyBanner } from '@/lib/plan'
import { EVENT_REMOVED_NOTE, showEventRemovedNote } from '@/lib/entryNotes'
import { AI_BUTTON_LABELS, NEEDS_MORE_INPUT, aiButtonState } from '@/lib/aiButton'

const MAX_POLL_FAILURES = 5
const AI_STATUS_POLL_MS = 3000

const AI_NOTICES = {
  writing: 'AI is writing this entry. Wait a moment for the draft, or edit anyway.',
  queued: 'AI will write this entry shortly. Wait for the draft, or edit anyway.',
} as const
// For someone who cannot edit the entry: there is no Edit to fall back on.
const AI_NOTICES_NO_EDIT = {
  writing: 'AI is writing this entry.',
  queued: 'AI will write this entry shortly.',
} as const
// Polling gave up, so the status may be stale.
const AI_NOTICE_STALLED = "Couldn't check on the AI draft. Refresh the page to see it."
// The AI notice and the mid-edit warning share one callout: an accent border and body-size text.
const AI_CALLOUT_STYLE: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem',
  padding: '0.75rem 1rem', fontSize: '0.95rem',
  border: '1px solid var(--accent)', borderLeftWidth: 4, borderRadius: 6,
}

function planDetails(plan: OwnerPlan & { ai_drafts_limit: number }) {
  return [
    {
      label: 'AI drafts',
      text: `${plan.ai_drafts_left ?? 0} of ${plan.ai_drafts_limit} left · resets ${formatResetDate(plan.resets_on)}`,
    },
    {
      label: 'Photos per entry',
      text: plan.photos_per_entry == null ? 'Unlimited' : String(plan.photos_per_entry),
    },
  ]
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
  // Only GET /entries/{id} carries the plan; action responses have plan: null.
  // Keep the last real one so an action never blanks the badge or re-enables a grayed control.
  // Same for the owner's temperature unit: action responses carry temperature_unit: null,
  // so keep the last real one rather than falling back to °F. Taken in the render that
  // sees a new entry (row 61: state, not a ref written during render).
  const [plan, setPlan] = useState<OwnerPlan | null>(null)
  const [unit, setUnit] = useState<TemperatureUnit>('F')
  const [seenEntry, setSeenEntry] = useState<Entry | null>(null)
  if (entry !== seenEntry) {
    setSeenEntry(entry)
    if (entry?.plan) setPlan(entry.plan)
    if (entry?.temperature_unit) setUnit(entry.temperature_unit)
  }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)

  const [editEntryDate, setEditEntryDate] = useState('')
  const [editEntryEndDate, setEditEntryEndDate] = useState('')
  const [editDateError, setEditDateError] = useState('')

  const [publishing, setPublishing] = useState(false)
  const [pollingRegen, setPollingRegen] = useState(false)
  const [regenStartedAt, setRegenStartedAt] = useState<string | null>(null)
  const [regenStartedGenAt, setRegenStartedGenAt] = useState<string | null>(null)
  const [regenStartTime, setRegenStartTime] = useState<string | null>(null)
  const [regenResult, setRegenResult] = useState<'success' | 'failed' | null>(null)
  const [regenSlow, setRegenSlow] = useState(false)
  const [regenErrorMessage, setRegenErrorMessage] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [aiFailures, setAiFailures] = useState(0)
  const aiInFlightRef = useRef(false)
  // What the entry held when Edit was clicked, and the latest server copy seen mid-edit.
  const [editBase, setEditBase] = useState<{ title: string; body: string } | null>(null)
  const [serverEntry, setServerEntry] = useState<Entry | null>(null)
  const editingRef = useLatest(editing)
  const [answeringSuggestion, setAnsweringSuggestion] = useState(false)

  const [noticeKey, setNoticeKey] = useState(0)
  // A Google Photos pick was just started in a new tab: watch for the import it creates.
  const [pickerOpening, setPickerOpening] = useState(false)
  const [importInFlight, setImportInFlight] = useState(false)
  const [importCopying, setImportCopying] = useState(false)
  const googleMsg = googleStatusMessage({
    status: searchParams.get('google'),
    missing: searchParams.get('missing'),
    reason: searchParams.get('reason'),
    expected: searchParams.get('expected'),
    page: 'entry',
  })

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set())

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
        setEditEntryDate(e.entry_date)
        setEditEntryEndDate(e.entry_end_date ?? '')
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
    setEditEntryDate(entry.entry_date)
    setEditEntryEndDate(entry.entry_end_date ?? '')
    setEditDateError('')
    setEditBase({ title: entry.title ?? '', body: entry.body_markdown ?? '' })
    setServerEntry(null)
    setEditing(true)
  }

  function handleCancelEdit() {
    // Show whatever the AI wrote while the form was open.
    if (serverEntry) setEntry(serverEntry)
    setServerEntry(null)
    setEditing(false)
  }

  const aiLandedMidEdit =
    editing && !!serverEntry && !serverEntry.ai_status && !!editBase &&
    ((serverEntry.body_markdown ?? '') !== editBase.body || (serverEntry.title ?? '') !== editBase.title)

  async function handleSave() {
    if (!entry) return

    setEditDateError('')

    if (!editEntryDate) {
      setEditDateError('Start date is required')
      return
    }
    if (editEntryEndDate && editEntryEndDate < editEntryDate) {
      setEditDateError('End date must be on or after start date')
      return
    }

    setSaving(true)
    try {
      const updated = await api.entries.patch(entry.id, {
        title: editTitle || null,
        body_markdown: editBody || null,
        entry_date: editEntryDate,
        entry_end_date: editEntryEndDate || null,
      })
      setEntry(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError(limitMessage(e, plan) ?? (e instanceof Error ? e.message : 'Save failed'))
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
      setError(limitMessage(e, plan) ?? (e instanceof Error ? e.message : 'Publish failed'))
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
      setError(limitMessage(e, plan) ?? (e instanceof Error ? e.message : 'Unpublish failed'))
    } finally {
      setPublishing(false)
    }
  }

  async function handleRegenerate() {
    if (!entry) return
    try {
      const regenStartedGenAt = entry.last_generation?.created_at ?? null
      setRegenStartedAt(entry.updated_at)
      setRegenStartedGenAt(regenStartedGenAt)
      setRegenStartTime(new Date().toISOString())
      setRegenResult(null)
      setRegenSlow(false)
      setRegenErrorMessage(null)
      const queued = await api.entries.regenerate(entry.id)
      // The response says whether the draft is queued; the timeout below reads it.
      setEntry((current) => (current ? { ...current, ai_status: queued.ai_status ?? null } : current))
      setPollingRegen(true)
      setError('')
    } catch (e: unknown) {
      setError(limitMessage(e, plan) ?? (e instanceof Error ? e.message : 'Regenerate failed'))
    }
  }

  const pollRegen = useCallback(async () => {
    if (!entry) return
    try {
      const updated = await api.entries.get(entry.id)
      // Prefer terminating on last_generation.created_at change. Without a
      // generation record, wait for text: the draft job works out the entry's
      // place first (item 24), which changes updated_at before any draft exists.
      const genChanged = updated.last_generation != null
        ? updated.last_generation.created_at !== regenStartedGenAt
        : updated.updated_at !== regenStartedAt && !!updated.body_markdown

      if (genChanged) {
        setEntry(updated)
        setPollingRegen(false)
        setRegenSlow(false)

        if (updated.last_generation != null) {
          if (updated.last_generation.status === 'success') {
            setRegenResult('success')
            setRegenErrorMessage(null)
          } else {
            setRegenResult(null)
            setRegenErrorMessage(updated.last_generation.error ?? 'Generation failed')
          }
        } else {
          // No last_generation: fall back to old success behavior
          setRegenResult('success')
        }
      } else {
        // Keep the AI status fresh, so the timeout knows whether a draft is still coming.
        setEntry((current) =>
          current && current.ai_status !== updated.ai_status
            ? { ...current, ai_status: updated.ai_status ?? null }
            : current,
        )
      }
    } catch {
    }
  }, [entry, regenStartedAt, regenStartedGenAt])

  usePolling(pollRegen, 2000, pollingRegen)

  // A queued or running AI draft: poll until it finishes.
  const aiPending = !!entry?.ai_status && !pollingRegen
  const pollAi = useCallback(async () => {
    if (!entry || aiInFlightRef.current) return
    aiInFlightRef.current = true
    try {
      const updated = await api.entries.get(entry.id)
      // Mid-edit, take only the AI fields so nothing typed is touched.
      if (editingRef.current) setServerEntry(updated)
      setEntry((current) =>
        editingRef.current && current
          ? { ...current, ai_status: updated.ai_status, suggestion: updated.suggestion, last_generation: updated.last_generation }
          : updated,
      )
      setAiFailures(0)
    } catch {
      setAiFailures((n) => n + 1)
    } finally {
      aiInFlightRef.current = false
    }
  }, [entry, editingRef])

  usePolling(pollAi, AI_STATUS_POLL_MS, aiPending && aiFailures < MAX_POLL_FAILURES)

  const regenTimedOut = useEffectEvent(() => {
    setPollingRegen(false)
    setRegenSlow(false)
    if (entry?.ai_status) {
      // Still queued or being written: end the panel and let the AI notice
      // and its polling show the draft when it lands.
      setRegenResult(null)
      setRegenStartTime(null)
    } else {
      setRegenResult('failed')
    }
  })

  useEffect(() => {
    if (!pollingRegen) return
    const slowTimer = setTimeout(() => setRegenSlow(true), 9 * 1000)
    const failTimer = setTimeout(regenTimedOut, 60 * 1000)
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

  async function answerSuggestion(answer: 'accept' | 'dismiss') {
    if (!entry) return
    setAnsweringSuggestion(true)
    try {
      const updated = answer === 'accept'
        ? await api.entries.acceptSuggestion(entry.id)
        : await api.entries.dismissSuggestion(entry.id)
      setEntry(updated)
      setRegenResult(null)
      setRegenStartTime(null)
    } catch (e: unknown) {
      setError(limitMessage(e, plan) ?? (e instanceof Error ? e.message : 'Could not update the suggestion'))
    } finally {
      setAnsweringSuggestion(false)
    }
  }

  const refreshAfterImport = useCallback(() => {
    api.entries.get(entryId).then(setEntry).catch(() => {})
  }, [entryId])

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
  const readOnly = plan?.read_only ?? false
  const aiStalled = aiFailures >= MAX_POLL_FAILURES
  const aiOut = plan?.ai_drafts_left === 0
  const aiButton = aiButtonState(entry)
  const photoRoom = plan == null || plan.photos_per_entry == null
    ? null
    : Math.max(0, plan.photos_per_entry - entry.photos.length)

  return (
    <>
      <AppHeader back={{ href: diaryHref, label: '← Diary' }} />

      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        {googleMsg && (
          <p className={googleMsg.isError ? 'error-message' : 'success-message'} style={{ marginBottom: '1rem' }}>
            {googleMsg.text}
          </p>
        )}

        {readOnly && plan && <p className="error-message" style={{ marginBottom: '1rem' }}>{readOnlyBanner(plan.plan_name)}</p>}
        {plan && plan.ai_drafts_limit != null && (
          <div style={{ marginBottom: '0.75rem' }}>
            <PlanBadge
              label="AI drafts"
              used={plan.ai_drafts_limit - (plan.ai_drafts_left ?? 0)}
              limit={plan.ai_drafts_limit}
              resetsOn={plan.resets_on}
              details={planDetails(plan as OwnerPlan & { ai_drafts_limit: number })}
            />
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem', flexWrap: 'wrap' }}>
          <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
          <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{formatDateRange(entry.entry_date, entry.entry_end_date)}</span>
          <WeatherBadge enrichments={entry.enrichments ?? []} unit={unit} />
        </div>
        {!editing && <EntryPlace entry={entry} onUpdated={setEntry} readOnly={readOnly} />}
        {!editing && entry.can_edit && (
          <AddPhotos
            entry={entry}
            onChanged={setEntry}
            room={readOnly ? 0 : photoRoom}
            googlePhotos={<GooglePhotosPick entry={entry} inFlight={importInFlight} copying={importCopying} disabled={readOnly || photoRoom === 0} onStarted={() => setNoticeKey((k) => k + 1)} onOpeningChange={setPickerOpening} />}
          />
        )}

        {editing ? (
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-date">Start date</label>
              <input
                id="entry-date"
                type="date"
                value={editEntryDate}
                onChange={(e) => setEditEntryDate(e.target.value)}
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-end-date">End date (optional)</label>
              <input
                id="entry-end-date"
                type="date"
                value={editEntryEndDate}
                onChange={(e) => setEditEntryEndDate(e.target.value)}
              />
            </div>
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
            {editDateError && (
              <p className="error-message" style={{ marginBottom: '0.5rem' }}>{editDateError}</p>
            )}
            {aiLandedMidEdit && (
              <p role="status" style={AI_CALLOUT_STYLE}>
                The AI draft arrived while you were editing. Save to keep your text instead, or Cancel to read the AI draft.
              </p>
            )}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving || !editEntryDate}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={handleCancelEdit} disabled={saving}>
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

            {showEventRemovedNote(entry) && <LimitNote>{EVENT_REMOVED_NOTE}</LimitNote>}

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

            {entry.last_generation?.error === 'ai_draft_limit' && aiOut && plan && (
              <LimitNote>{aiEntryNote(plan.resets_on)}</LimitNote>
            )}

            {regenErrorMessage && (
              <div
                className="bg-red-50 border border-red-200 text-red-800 rounded p-3 text-sm flex items-start gap-2"
                style={{ marginBottom: '1rem' }}
              >
                <span style={{ flex: 1 }}>
                  {regenErrorMessage === 'ai_draft_limit' && plan
                    ? aiLimitNote(plan.resets_on)
                    : `AI generation failed: ${regenErrorMessage}. Your text was not changed.`}
                </span>
                <button
                  onClick={() => setRegenErrorMessage(null)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, lineHeight: 1, flexShrink: 0 }}
                  aria-label="Dismiss"
                >
                  ×
                </button>
              </div>
            )}

            {entry.ai_status && !pollingRegen && (
              <div role="status" data-testid="ai-notice" style={AI_CALLOUT_STYLE}>
                {aiStalled ? null : entry.ai_status === 'writing' ? (
                  <span className="spinner" aria-hidden="true" style={{ width: 16, height: 16 }} />
                ) : (
                  <span aria-hidden="true">⏱</span>
                )}
                <span>
                  {aiStalled
                    ? AI_NOTICE_STALLED
                    : (entry.can_edit && !readOnly ? AI_NOTICES : AI_NOTICES_NO_EDIT)[entry.ai_status]}
                </span>
              </div>
            )}

            {entry.body_markdown ? (
              <div
                className="card"
                data-testid="entry-body"
                style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7, fontSize: '0.95rem' }}
              >
                {entry.body_markdown}
              </div>
            ) : (
              <div className="empty-state">
                <p>No content yet. Trigger a scan or regenerate to generate a draft.</p>
              </div>
            )}

            {entry.suggestion && (
              <SuggestionBox
                suggestion={entry.suggestion}
                busy={answeringSuggestion}
                readOnly={readOnly}
                onAccept={() => answerSuggestion('accept')}
                onDismiss={() => answerSuggestion('dismiss')}
              />
            )}

          </>
        )}

        {/* Stays mounted in Edit mode so polling and the photo refresh keep working; just not shown. */}
        {entry.can_edit && (
          <div hidden={editing}>
            <PhotoImportNotice
              entryId={entry.id}
              refreshKey={noticeKey}
              watching={pickerOpening}
              onFinished={refreshAfterImport}
              onInFlightChange={(inFlight, copying) => {
                setImportInFlight(inFlight)
                setImportCopying(copying)
              }}
            />
          </div>
        )}

        {!editing && (
          <>
            {/* Photo strip */}
            {entry.photos && entry.photos.length > 0 && (
              <section>
                <h3>Photos</h3>
                <ul className="photo-grid">
                  {entry.photos.map((p, i) => (
                    <li key={p.id}>
                      <PhotoThumbnail
                        photoId={p.id}
                        alt=""
                        onClick={() => setLightboxIndex(i)}
                        className="thumbnail"
                      />
                      <button
                        type="button"
                        className="thumbnail-action"
                        aria-label="Remove photo from entry"
                        disabled={removingIds.has(p.id) || readOnly}
                        onClick={async (e) => {
                          e.stopPropagation();
                          if (removingIds.has(p.id)) return;
                          setRemovingIds(prev => new Set(prev).add(p.id))
                          try {
                            await api.photos.detachFromEntry(entry.id, p.id)
                            const refreshed = await api.entries.get(entry.id)
                            setEntry(refreshed)
                          } catch (err: unknown) {
                            setError(limitMessage(err, plan) ?? (err instanceof Error ? err.message : 'Failed to remove photo'))
                          } finally {
                            setRemovingIds(prev => { const next = new Set(prev); next.delete(p.id); return next; })
                          }
                        }}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {lightboxIndex !== null && entry.photos && (
              <PhotoLightbox
                photoIds={entry.photos.map(p => p.id)}
                index={lightboxIndex}
                onIndexChange={setLightboxIndex}
                onClose={() => setLightboxIndex(null)}
              />
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
                        {formatEventTime(event, entry.tz_override ?? undefined)} — {event.summary || '(no title)'}
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
                      ? (entry.suggestion ? 'Suggestion ready below your text' : 'Draft generated')
                      : 'Generation is taking longer than expected — refresh the page to check'
                }
                startedAt={regenStartTime ?? undefined}
                onDismiss={() => { setRegenResult(null); setRegenStartTime(null) }}
              />
            )}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1.25rem', flexWrap: 'wrap' }}>
              <button className="btn btn-secondary" onClick={startEdit} disabled={readOnly}>
                Edit
              </button>
              {entry.status === 'draft' ? (
                <button className="btn btn-primary" onClick={handlePublish} disabled={publishing || readOnly}>
                  {publishing ? 'Publishing…' : 'Publish'}
                </button>
              ) : (
                <button className="btn btn-secondary" onClick={handleUnpublish} disabled={publishing || readOnly}>
                  {publishing ? 'Unpublishing…' : 'Unpublish'}
                </button>
              )}
              <button
                className="btn btn-secondary"
                onClick={handleRegenerate}
                disabled={pollingRegen || readOnly || aiOut || aiButton.needsMoreInput}
                aria-describedby={aiOut && plan ? 'ai-limit-note' : undefined}
                title={aiButton.needsMoreInput ? NEEDS_MORE_INPUT : undefined}
              >
                {AI_BUTTON_LABELS[aiButton.mode][pollingRegen ? 'running' : 'idle']}
              </button>
              <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
            {aiOut && plan && <LimitNote id="ai-limit-note">{aiLimitNote(plan.resets_on)}</LimitNote>}
          </>
        )}
      </div>
    </>
  )
}
