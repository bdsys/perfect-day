'use client'

import type { Suggestion } from '@/lib/api'

const SOURCE_LABEL: Record<Suggestion['body_source'], string> = {
  llm: 'a fresh draft from your calendar',
  llm_hybrid: 'your text and the calendar, combined',
  llm_polished: 'your text, tidied up',
}

function timeAgo(iso: string, now: Date = new Date()): string {
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  return new Date(iso).toLocaleDateString()
}

/**
 * The AI's pending version of an entry that already has text (follow-up 25.3).
 * It never replaces the text by itself: the person accepts it, dismisses it,
 * or copies the parts they want.
 */
export function SuggestionBox({
  suggestion,
  busy,
  onAccept,
  onDismiss,
}: {
  suggestion: Suggestion
  busy: boolean
  onAccept: () => void
  onDismiss: () => void
}) {
  const flagged = suggestion.flagged_tokens
  return (
    <section
      aria-labelledby="suggestion-heading"
      style={{
        marginTop: '1rem',
        background: '#f4f7fe',
        border: '1px solid #c7d6f7',
        borderRadius: 8,
        padding: '1rem 1.25rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1d4ed8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 20h9" />
          <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
        </svg>
        <h2 id="suggestion-heading" style={{ margin: 0, fontSize: '0.95rem', fontWeight: 600, color: '#1e3a8a' }}>
          The AI suggests a new version
        </h2>
        <span style={{ fontSize: '0.8rem', color: '#4b5563' }}>
          · {SOURCE_LABEL[suggestion.body_source]} · {timeAgo(suggestion.created_at)}
        </span>
      </div>

      {flagged.length > 0 && (
        <div style={{ background: '#fffbeb', border: '1px solid #f59e0b', borderRadius: 6, padding: '0.5rem 0.75rem', fontSize: '0.8rem', color: '#92400e' }}>
          <strong>Check before accepting:</strong> this version mentions{' '}
          <strong>{flagged.join(', ')}</strong>, which are not in your calendar or your text.
        </div>
      )}

      <div style={{ background: '#fff', border: '1px solid #c7d6f7', borderRadius: 6, padding: '1rem', userSelect: 'text', cursor: 'text' }}>
        {suggestion.title && (
          <p style={{ margin: '0 0 0.5rem', fontWeight: 700, fontSize: '1.05rem' }}>{suggestion.title}</p>
        )}
        <p data-testid="suggestion-body" style={{ margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
          {suggestion.body_markdown}
        </p>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" style={{ minHeight: 44 }} onClick={onAccept} disabled={busy}>
          Accept
        </button>
        <button className="btn btn-secondary" style={{ minHeight: 44 }} onClick={onDismiss} disabled={busy}>
          Dismiss
        </button>
        <span style={{ fontSize: '0.8rem', color: '#4b5563' }}>
          Accept replaces your title and text. Or select any part above and copy it.
        </span>
      </div>
    </section>
  )
}
