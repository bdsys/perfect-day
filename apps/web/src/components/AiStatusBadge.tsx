import type { AiStatus } from '@/lib/api'

/** Shows that an entry's AI draft is queued or being written; nothing when idle. */
export function AiStatusBadge({ status }: { status: AiStatus | null }) {
  if (!status) return null
  return (
    <span
      role="status"
      data-testid="ai-status"
      style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.75rem', color: '#555' }}
    >
      {status === 'writing' ? (
        <span className="spinner" aria-hidden="true" style={{ width: 12, height: 12 }} />
      ) : (
        <span aria-hidden="true">⏱</span>
      )}
      {status === 'writing' ? 'AI is writing…' : 'Queued'}
    </span>
  )
}
