'use client'

import { useState } from 'react'

export const DISPLAY_NAME_LABEL = 'Your name, as people with the link will see it'

interface Props {
  initial?: string
  submitLabel: string
  busy: boolean
  onSubmit: (name: string) => void
  onCancel?: () => void
}

/** Asks the owner for the name a wish list's link shows ("Shared by ..."). */
export default function DisplayNameForm({ initial = '', submitLabel, busy, onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial)
  return (
    <form
      onSubmit={(ev) => {
        ev.preventDefault()
        onSubmit(name.trim())
      }}
      style={{ marginBottom: '1rem' }}
    >
      <label>
        {DISPLAY_NAME_LABEL}
        <input type="text" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
          {submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
