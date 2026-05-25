'use client'
import { useEffect, useState } from 'react'
import { Spinner } from './Spinner'

type StatusPanelProps = {
  state: 'running' | 'success' | 'partial' | 'failed'
  headline: string
  detail?: string
  errors?: string[]
  startedAt?: string
  onDismiss?: () => void
}

function formatElapsed(startedAt: string): string {
  const elapsed = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
  const m = Math.floor(elapsed / 60)
  const s = elapsed % 60
  return m > 0 ? `${m}m ${s}s ago` : `${s}s ago`
}

export function StatusPanel({ state, headline, detail, errors, startedAt, onDismiss }: StatusPanelProps) {
  const [elapsed, setElapsed] = useState(startedAt ? formatElapsed(startedAt) : '')

  useEffect(() => {
    if (state !== 'running' || !startedAt) return
    setElapsed(formatElapsed(startedAt))
    const id = setInterval(() => setElapsed(formatElapsed(startedAt)), 1000)
    return () => clearInterval(id)
  }, [state, startedAt])

  useEffect(() => {
    if (state !== 'success') return
    const id = setTimeout(() => onDismiss?.(), 8000)
    return () => clearTimeout(id)
  }, [state, onDismiss])

  const icon =
    state === 'running' ? <Spinner size={16} /> :
    state === 'success' ? '✓' :
    state === 'partial' ? '⚠' :
    '✕'

  return (
    <div className={`status-panel ${state}`}>
      <span>{icon}</span>
      <div className="status-panel-body">
        <div className="status-panel-headline">{headline}</div>
        {detail && <div className="status-panel-detail">{detail}</div>}
        {state === 'running' && startedAt && (
          <div className="status-panel-detail">{elapsed}</div>
        )}
        {errors && errors.length > 0 && (
          <ul className="status-panel-errors">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}
      </div>
      {state !== 'running' && onDismiss && (
        <button className="status-panel-dismiss" onClick={onDismiss} aria-label="Dismiss">×</button>
      )}
    </div>
  )
}
