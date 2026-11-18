'use client'

import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth-context'
import { formatDeadline } from '@/lib/deadline'
import { navigateTo } from '@/lib/navigate'

/** Shown on every route while the account is within its deletion grace period. */
export default function RestoreAccountScreen() {
  const { user, refreshUser, restore, logout } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const deadline = user?.deletion_scheduled_for ?? null

  // A 403 can arrive before /me has told us the deadline: fetch it.
  useEffect(() => {
    if (!deadline) refreshUser().catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleRestore() {
    setError('')
    setBusy(true)
    try {
      await restore()
    } catch {
      setError("Couldn't restore your account. Try again.")
      setBusy(false)
    }
  }

  async function handleSignOut() {
    setBusy(true)
    try {
      await logout()
    } finally {
      navigateTo('/login')
    }
  }

  return (
    <div className="container" style={{ maxWidth: 400, paddingTop: '4rem' }}>
      <div className="card">
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '1rem' }}>Perfect Day</h1>
        <p style={{ marginBottom: '1.5rem' }}>
          {deadline
            ? `Your account will be deleted on ${formatDeadline(deadline)}.`
            : 'Your account is scheduled for deletion.'}
        </p>
        {error && <p role="alert" className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="btn btn-primary" onClick={handleRestore} disabled={busy}>
            Restore my account
          </button>
          <button className="btn btn-secondary" onClick={handleSignOut} disabled={busy}>
            Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
