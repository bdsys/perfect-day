import type { AiStatus } from '@/lib/api'

/**
 * Shows that an entry's AI draft is queued or being written; nothing when idle.
 * *stalled* is for when polling gave up: the status may be stale, so the badge
 * stops animating and asks for a refresh instead.
 */
export function AiStatusBadge({ status, stalled = false }: { status: AiStatus | null; stalled?: boolean }) {
  if (!status) return null
  return (
    <span
      role="status"
      data-testid="ai-status"
      style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.75rem', color: '#555' }}
    >
      {stalled ? null : status === 'writing' ? (
        <span className="spinner" aria-hidden="true" style={{ width: 12, height: 12 }} />
      ) : (
        <span aria-hidden="true">⏱</span>
      )}
      {stalled ? 'Refresh to check' : status === 'writing' ? 'AI is writing…' : 'Queued'}
    </span>
  )
}
