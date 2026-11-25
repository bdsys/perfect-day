'use client'

import { useState } from 'react'
import type { WishList } from '@/lib/api'

interface Props {
  list: WishList
  busy: boolean
  readOnly: boolean
  onLinkOff: () => void
  onNewLink: () => void
  onError: (message: string) => void
}

/** The public link of a published list. Turning it off stays possible on a read-only diary. */
export default function WishListLinkBox({ list, busy, readOnly, onLinkOff, onNewLink, onError }: Props) {
  const [copied, setCopied] = useState(false)
  const url = list.link ? `${window.location.origin}${list.link.path}` : ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
    } catch {
      onError("Couldn't copy the link. Select it and copy it by hand.")
    }
  }

  return (
    <section style={{ marginBottom: '1.5rem' }}>
      <h2>Link</h2>
      {list.link ? (
        <>
          <input type="text" readOnly value={url} aria-label="Wish list link" onFocus={(e) => e.target.select()} />
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-secondary" onClick={copy}>
              {copied ? 'Copied' : 'Copy link'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => { setCopied(false); onLinkOff() }} disabled={busy}>
              Turn link off
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => { setCopied(false); onNewLink() }} disabled={busy || readOnly}>
              Make a new link
            </button>
          </div>
        </>
      ) : (
        <>
          <p>The link is off. Nobody can open the list until you make a new one.</p>
          <button type="button" className="btn btn-secondary" onClick={onNewLink} disabled={busy || readOnly}>
            Make a new link
          </button>
        </>
      )}
    </section>
  )
}
