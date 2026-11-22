'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import AppHeader from '@/components/AppHeader'
import LimitNote from '@/components/LimitNote'
import { api, inviteUrl, type Diary, type Invitation, type Member, type MyPlan } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatShortDate } from '@/lib/date'
import { limitMessage, peopleCounter, peopleLimitNote } from '@/lib/plan'

const roleLabel = (role: string) => role.charAt(0).toUpperCase() + role.slice(1)

function inviteErrorMessage(e: unknown, plan: MyPlan | null): string {
  const limit = limitMessage(e, plan)
  if (limit) return limit
  switch ((e as { code?: string } | null)?.code) {
    case 'already_invited':
      return 'That person already has an invite. Use New link to send it again.'
    case 'already_member':
      return 'That person already has access.'
    case 'cannot_invite_self':
      return "That's your own address."
  }
  return e instanceof Error ? e.message : 'Something went wrong'
}

export default function SharingPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [plan, setPlan] = useState<MyPlan | null>(null)
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('viewer')
  const [busy, setBusy] = useState(false)
  // The raw token lives only here: never in storage or the URL.
  const [link, setLink] = useState<{ email: string; url: string } | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  const refreshPeople = useCallback(async () => {
    const [d, inv, mem] = await Promise.all([
      api.diaries.get(diaryId),
      api.sharing.invitations(diaryId),
      api.sharing.members(diaryId),
    ])
    setDiary(d)
    setInvitations(inv)
    setMembers(mem)
  }, [diaryId])

  useEffect(() => {
    if (!user || !diaryId) return
    ;(async () => {
      try {
        const d = await api.diaries.get(diaryId)
        if (d.role !== 'owner') {
          router.replace(`/diaries/${diaryId}`)
          return
        }
        const [p, inv, mem] = await Promise.all([
          api.auth.myPlan(),
          api.sharing.invitations(diaryId),
          api.sharing.members(diaryId),
        ])
        setDiary(d)
        setPlan(p)
        setInvitations(inv)
        setMembers(mem)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to load sharing')
      } finally {
        setLoading(false)
      }
    })()
  }, [user, diaryId, router])

  async function run(action: () => Promise<void>) {
    setError('')
    setBusy(true)
    try {
      await action()
    } catch (e: unknown) {
      setError(inviteErrorMessage(e, plan))
    } finally {
      setBusy(false)
    }
  }

  function showLink(inv: Invitation) {
    setCopied(false)
    setLink(inv.token ? { email: inv.invited_email, url: inviteUrl(inv.token) } : null)
  }

  const makeInvite = (ev: React.FormEvent) => {
    ev.preventDefault()
    return run(async () => {
      const inv = await api.sharing.invite(diaryId, email.trim(), plan?.editors ? role : 'viewer')
      showLink(inv)
      setEmail('')
      await refreshPeople()
    })
  }

  const newLink = (inv: Invitation) =>
    run(async () => {
      showLink(await api.sharing.newLink(diaryId, inv.id))
      await refreshPeople()
    })

  const cancelInvite = (inv: Invitation) =>
    run(async () => {
      await api.sharing.revoke(diaryId, inv.id)
      if (link?.email === inv.invited_email) setLink(null)
      await refreshPeople()
    })

  const removeMember = (m: Member) => {
    if (!confirm(`Remove ${m.name}? They won't see ${diary?.name ?? 'this diary'} any more.`)) return
    return run(async () => {
      await api.sharing.remove(diaryId, m.user_id)
      await refreshPeople()
    })
  }

  const changeRole = (m: Member, newRole: string) =>
    run(async () => {
      await api.sharing.setRole(diaryId, m.user_id, newRole)
      await refreshPeople()
    })

  async function copyLink() {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link.url)
      setCopied(true)
    } catch {
      setError("Couldn't copy the link. Select it and copy it by hand.")
    }
  }

  async function shareLink() {
    if (!link) return
    try {
      await navigator.share({ url: link.url })
    } catch {
      // Cancelling the share sheet is not an error.
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  const people = diary?.people ?? null
  const atLimit = people != null && people.limit != null && people.used >= people.limit
  const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function'
  const editorsOk = plan?.editors ?? false

  return (
    <>
      <AppHeader />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <p>
          <Link href={`/diaries/${diaryId}`}>← {diary?.name ?? 'Diary'}</Link>
        </p>
        <h1 className="page-title">Sharing</h1>
        {people && <p>{peopleCounter(people.used, people.limit)}</p>}

        {error && <p className="error-message" role="alert" style={{ marginBottom: '1rem' }}>{error}</p>}

        {atLimit && people?.limit != null ? (
          <div style={{ marginBottom: '1.5rem' }}>
            <LimitNote>{peopleLimitNote(plan?.plan_name ?? null, people.limit)}</LimitNote>
            <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
          </div>
        ) : (
          <form onSubmit={makeInvite} style={{ marginBottom: '1.5rem' }}>
            <label>
              Email
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            {editorsOk && (
              <label>
                Role
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="viewer">Viewer</option>
                  <option value="editor">Editor</option>
                </select>
              </label>
            )}
            <button type="submit" className="btn btn-primary" disabled={busy || !email.trim()}>
              Make invite link
            </button>
          </form>
        )}

        {link && (
          <div style={{ marginBottom: '1.5rem' }}>
            <p>{`Invite link for ${link.email}. It works once, for 7 days.`}</p>
            <input type="text" readOnly value={link.url} aria-label="Invite link" onFocus={(e) => e.target.select()} />
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button type="button" className="btn btn-secondary" onClick={copyLink}>
                {copied ? 'Copied' : 'Copy link'}
              </button>
              {canShare && (
                <button type="button" className="btn btn-secondary" onClick={shareLink}>
                  Share…
                </button>
              )}
            </div>
          </div>
        )}

        {invitations.length > 0 && (
          <section style={{ marginBottom: '1.5rem' }}>
            <h2>Invites</h2>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {invitations.map((inv) => (
                <li key={inv.id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>
                    {`${inv.invited_email} · ${roleLabel(inv.role)} · ${
                      inv.status === 'expired' ? 'Expired' : `expires ${formatShortDate(inv.expires_at)}`
                    }`}
                  </span>
                  <button type="button" className="btn btn-secondary" onClick={() => newLink(inv)} disabled={busy}>
                    New link
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => cancelInvite(inv)} disabled={busy}>
                    Cancel invite
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {members.length > 0 && (
          <section>
            <h2>People</h2>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {members.map((m) => {
                const needsFamily = m.role === 'editor' && m.effective_role !== 'editor'
                return (
                  <li key={m.user_id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    {editorsOk ? (
                      <>
                        <span>{m.name}</span>
                        <select
                          aria-label={`Role for ${m.name}`}
                          value={m.role}
                          onChange={(e) => changeRole(m, e.target.value)}
                          disabled={busy}
                        >
                          <option value="viewer">Viewer</option>
                          <option value="editor">Editor</option>
                        </select>
                      </>
                    ) : (
                      <span>{`${m.name} · ${needsFamily ? 'Editor — needs Family' : roleLabel(m.role)}`}</span>
                    )}
                    <button type="button" className="btn btn-secondary" onClick={() => removeMember(m)} disabled={busy}>
                      Remove
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        )}
      </div>
    </>
  )
}
