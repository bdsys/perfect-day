'use client'

import { useState } from 'react'
import { api, type DeletionNotice as Notice } from '@/lib/api'
import { formatShortDate } from '@/lib/date'

/** Shown to members while the owner's account is scheduled for deletion. */
export default function DeletionNotice({
  notice,
  diaryId,
  diaryName,
  onDeclined,
}: {
  notice: Notice
  diaryId: string
  diaryName: string
  onDeclined: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const date = formatShortDate(notice.deletes_on)

  async function decline() {
    if (!confirm(`Decline? ${diaryName} will be deleted with ${notice.owner_name}'s account on ${date}.`)) return
    setBusy(true)
    setError('')
    try {
      await api.sharing.decline(diaryId)
      onDeclined()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Decline failed')
      setBusy(false)
    }
  }

  return (
    <div className="error-message" role="status" style={{ marginBottom: '1rem' }}>
      {notice.becomes_yours ? (
        <>
          <p style={{ margin: 0 }}>
            {notice.owner_name} is deleting their account. <em>{diaryName}</em> becomes yours on {date}.
          </p>
          <button type="button" className="btn btn-secondary" style={{ marginTop: '0.5rem' }} onClick={decline} disabled={busy}>
            Decline
          </button>
          {error && <p style={{ margin: '0.5rem 0 0' }}>{error}</p>}
        </>
      ) : (
        <p style={{ margin: 0 }}>
          {notice.owner_name} is deleting their account. {diaryName} will be deleted on {date}.
        </p>
      )}
    </div>
  )
}
