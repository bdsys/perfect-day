'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, ApiError, type InvitePreview } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

type Outcome = { kind: 'preview'; preview: InvitePreview } | { kind: 'error'; message: string; code?: string }

function detail(err: ApiError, key: string, fallback: string): string {
  const v = err.details?.[key]
  return typeof v === 'string' && v ? v : fallback
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'invite_wrong_email') {
      const masked = detail(err, 'masked_email', 'another email address')
      const owner = detail(err, 'owner_name', 'the person who sent it')
      return `This invite is for ${masked}. Sign in with that address, or ask ${owner} for a new link.`
    }
    if (err.code === 'invite_expired') {
      return `This invite has expired. Ask ${detail(err, 'owner_name', 'the person who sent it')} for a new link.`
    }
    if (err.code === 'invite_not_found') {
      return "This invite link doesn't work any more. Ask the person who sent it for a new link."
    }
    return err.message
  }
  return err instanceof Error ? err.message : 'Something went wrong. Please try again.'
}

export default function InvitePage() {
  const { token } = useParams<{ token: string }>()
  const router = useRouter()
  const { user, loading: authLoading, logout } = useAuth()
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [accepting, setAccepting] = useState(false)
  const [acceptError, setAcceptError] = useState('')

  const signedIn = !authLoading && !!user

  useEffect(() => {
    if (!signedIn) return
    let cancelled = false
    api.sharing
      .preview(token)
      .then((preview) => {
        if (cancelled) return
        if (preview.status === 'owner' || preview.status === 'member') {
          router.replace(`/diaries/${preview.diary_id}`)
          return
        }
        setOutcome({ kind: 'preview', preview })
      })
      .catch((err: unknown) => {
        if (!cancelled) setOutcome({ kind: 'error', message: errorMessage(err), code: err instanceof ApiError ? err.code : undefined })
      })
    return () => {
      cancelled = true
    }
  }, [signedIn, token, router])

  async function handleAccept() {
    setAcceptError('')
    setAccepting(true)
    try {
      const { diary_id } = await api.sharing.accept(token)
      router.push(`/diaries/${diary_id}`)
    } catch (err: unknown) {
      setAcceptError(errorMessage(err))
      setAccepting(false)
    }
  }

  async function switchAccount() {
    try {
      await logout()
    } catch {
      // the sign-in page still works if the server call failed
    }
    router.push(`/login?next=${encodeURIComponent(`/invite/${token}`)}`)
  }

  if (authLoading) return <div className="loading">Loading…</div>

  const next = encodeURIComponent(`/invite/${token}`)

  return (
    <div className="container" style={{ maxWidth: 400, paddingTop: '4rem' }}>
      <div className="card">
        {!user ? (
          <>
            <p>You&apos;ve been invited to a diary on Perfect Day. Sign in or create an account to see it.</p>
            <p style={{ marginTop: '1rem', display: 'flex', gap: '1rem' }}>
              <Link href={`/login?next=${next}`}>Sign in</Link>
              <Link href={`/register?next=${next}`}>Create an account</Link>
            </p>
          </>
        ) : !outcome ? (
          <div className="loading">Loading…</div>
        ) : outcome.kind === 'error' ? (
          <>
            <p className="error-message">{outcome.message}</p>
            {outcome.code === 'invite_wrong_email' && (
              <button type="button" className="btn btn-secondary" onClick={switchAccount} style={{ marginTop: '1rem' }}>
                Sign out and use another account
              </button>
            )}
          </>
        ) : (
          <>
            <p>
              {outcome.preview.owner_name} shared <em>{outcome.preview.diary_name}</em> with you
            </p>
            {acceptError && <p className="error-message">{acceptError}</p>}
            <button
              type="button"
              className="btn btn-primary"
              disabled={accepting}
              onClick={handleAccept}
              style={{ marginTop: '1rem' }}
            >
              {accepting ? 'Opening…' : 'Open diary'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
