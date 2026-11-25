'use client'

import { useCallback, useEffect, useState } from 'react'
import { ApiError, parseApiError } from '@/lib/api'
import { formatListDate } from '@/lib/date'
import type { PublicItem, PublicWishListData } from '@/lib/publicApi'
import { claimCountText } from '@/components/WishListItemRow'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const STORAGE_KEY = 'pd-wish-claims'

type Remembered = Record<string, { claimId: string; secret: string }>

const ERROR_COPY: Record<string, string> = {
  item_taken: "Someone else just said they're getting this.",
  item_claims_full: "This item can't take anyone else.",
  invalid_name: 'Please type your name (up to 60 letters).',
  claim_not_found: 'That was already removed.',
}
const GENERIC = 'Something went wrong. Please try again.'

function readRemembered(): Remembered {
  try {
    const v = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {}
  } catch {
    return {}
  }
}

function writeRemembered(v: Remembered) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(v))
  } catch {
    // Private mode or blocked storage: claims still work, they just aren't remembered.
  }
}

function pastedListHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function errorCopy(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return 'Too many tries. Please wait a little and try again.'
    return (e.code && ERROR_COPY[e.code]) || (e.message && ERROR_COPY[e.message]) || GENERIC
  }
  return GENERIC
}

const isInactive = (e: unknown) =>
  e instanceof ApiError && (e.code === 'link_inactive' || e.message === 'link_inactive')

export default function PublicWishList({
  token,
  initial,
}: {
  token: string
  initial: PublicWishListData | null
}) {
  const [list, setList] = useState<PublicWishListData | null>(initial)
  const [dead, setDead] = useState(initial === null)
  const [remembered, setRemembered] = useState<Remembered>({})
  const [claimingId, setClaimingId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ itemId: string; text: string } | null>(null)

  // Read after mount so the server-rendered HTML and the first client render match.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setRemembered(readRemembered()), [])

  const base = `${API_BASE}/v1/public/${encodeURIComponent(token)}`

  const reload = useCallback(async () => {
    const res = await fetch(base, { cache: 'no-store' })
    if (!res.ok) throw await parseApiError(res)
    setList((await res.json()).wish_list)
  }, [base])

  function forget(itemId: string) {
    const next = { ...readRemembered() }
    delete next[itemId]
    writeRemembered(next)
    setRemembered(next)
  }

  async function run(itemId: string, action: () => Promise<void>, refetchOn: string[] = []) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      if (isInactive(e)) {
        setDead(true)
        return
      }
      setError({ itemId, text: errorCopy(e) })
      const code = e instanceof ApiError ? (e.code ?? e.message) : ''
      if (refetchOn.includes(code)) {
        try {
          await reload()
        } catch (e2) {
          if (isInactive(e2)) setDead(true)
        }
      }
    } finally {
      setBusy(false)
    }
  }

  function claim(item: PublicItem, ev: React.FormEvent) {
    ev.preventDefault()
    return run(item.id, async () => {
      const res = await fetch(`${base}/items/${encodeURIComponent(item.id)}/claims`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      })
      if (!res.ok) throw await parseApiError(res)
      const { claim_id, secret } = await res.json()
      const next = { ...readRemembered(), [item.id]: { claimId: claim_id, secret } }
      writeRemembered(next)
      setRemembered(next)
      setClaimingId(null)
      setName('')
      await reload()
    }, ['item_taken', 'item_claims_full'])
  }

  function undo(item: PublicItem) {
    const mine = remembered[item.id]
    if (!mine) return
    return run(item.id, async () => {
      const res = await fetch(`${base}/claims/${encodeURIComponent(mine.claimId)}`, {
        method: 'DELETE',
        headers: { 'X-Claim-Secret': mine.secret },
      })
      if (!res.ok) {
        const err = await parseApiError(res)
        if (err.code === 'claim_not_found' || err.message === 'claim_not_found') {
          forget(item.id)
          await reload().catch(() => undefined)
        }
        throw err
      }
      forget(item.id)
      await reload()
    })
  }

  if (dead || !list) {
    return (
      <main className="container" style={{ padding: '2rem 1rem' }}>
        <p>This link is no longer active</p>
      </main>
    )
  }

  return (
    <main className="container" style={{ padding: '2rem 1rem', maxWidth: 640 }}>
      <h1>{list.title}</h1>
      <p>{formatListDate(list.list_date)}</p>
      {list.shared_by && <p>Shared by {list.shared_by}</p>}
      {list.pasted_list_url && (
        <p>
          <a href={list.pasted_list_url} target="_blank" rel="noopener noreferrer">
            {`${list.title} list on ${pastedListHost(list.pasted_list_url)}`}
          </a>
        </p>
      )}
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {list.items.map((item) => {
          const mine = !!remembered[item.id] && item.claim_count >= 1
          const itemError = error?.itemId === item.id ? error.text : null
          return (
            <li key={item.id} style={{ marginBottom: '1rem' }}>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                <strong>{item.name}</strong>
                {item.url && (
                  <a href={item.url} target="_blank" rel="noopener noreferrer">See in store</a>
                )}
                {item.many && <span>As many as you like</span>}
              </div>
              {item.note && <p style={{ margin: '0.25rem 0' }}>{item.note}</p>}
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                {mine ? (
                  <>
                    <span>You&apos;re getting this</span>
                    <button type="button" className="btn btn-secondary" onClick={() => undo(item)} disabled={busy}>
                      Undo
                    </button>
                  </>
                ) : !item.taken && claimingId !== item.id ? (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => { setClaimingId(item.id); setName(''); setError(null) }}
                    disabled={busy}
                  >
                    I&apos;m getting this
                  </button>
                ) : item.taken && !item.many ? (
                  <span>Taken</span>
                ) : null}
                {item.many && item.claim_count > 0 && <span>{claimCountText(item.claim_count)}</span>}
              </div>
              {claimingId === item.id && !mine && (
                <form onSubmit={(ev) => claim(item, ev)} aria-label={`Claim ${item.name}`}>
                  <label>
                    Your name
                    <input
                      type="text"
                      required
                      maxLength={60}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </label>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>Save</button>
                    <button type="button" className="btn btn-secondary" onClick={() => setClaimingId(null)}>Cancel</button>
                  </div>
                </form>
              )}
              {itemError && <p role="alert" className="error-message">{itemError}</p>}
            </li>
          )
        })}
      </ul>
    </main>
  )
}
