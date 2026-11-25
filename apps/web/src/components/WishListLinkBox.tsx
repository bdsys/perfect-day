'use client'

import { useState } from 'react'
import DisplayNameForm from '@/components/DisplayNameForm'
import type { WishList } from '@/lib/api'

interface Props {
  list: WishList
  busy: boolean
  readOnly: boolean
  onLinkOff: () => void
  onNewLink: () => void
  onError: (message: string) => void
  /** The owner only: saves their name; resolves true when it was saved. */
  onChangeName?: (name: string) => Promise<boolean>
}

/** The public link of a published list. Turning it off stays possible on a read-only diary. */
export default function WishListLinkBox({ list, busy, readOnly, onLinkOff, onNewLink, onError, onChangeName }: Props) {
  const [copied, setCopied] = useState(false)
  const [editingName, setEditingName] = useState(false)
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
          {editingName && onChangeName ? (
            <DisplayNameForm
              initial={list.shared_by ?? ''}
              submitLabel="Save name"
              busy={busy}
              onSubmit={async (name) => {
                if (await onChangeName(name)) setEditingName(false)
              }}
              onCancel={() => setEditingName(false)}
            />
          ) : (
            <p style={{ marginTop: '0.5rem' }}>
              {list.shared_by
                ? `People with the link see: Shared by ${list.shared_by}`
                : "People with the link don't see a name."}
              {onChangeName && (
                <>
                  {' '}
                  <button type="button" className="btn btn-secondary" onClick={() => setEditingName(true)} disabled={busy}>
                    Change
                  </button>
                </>
              )}
            </p>
          )}
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
