'use client'

import { useState } from 'react'
import type { WishListItem, WishListItemFields } from '@/lib/api'

export function claimCountText(n: number): string {
  return n === 1 ? '1 person is getting this' : `${n} people are getting this`
}

export function WishListItemFormFields({
  value,
  onChange,
}: {
  value: { name: string; url: string; note: string; many: boolean }
  onChange: (v: { name: string; url: string; note: string; many: boolean }) => void
}) {
  return (
    <>
      <label>
        Item
        <input type="text" required maxLength={200} value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
      </label>
      <label>
        Store link (optional)
        <input type="url" maxLength={2048} value={value.url} onChange={(e) => onChange({ ...value, url: e.target.value })} />
      </label>
      <label>
        Note (optional)
        <input type="text" maxLength={500} value={value.note} onChange={(e) => onChange({ ...value, note: e.target.value })} />
      </label>
      <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <input type="checkbox" checked={value.many} onChange={(e) => onChange({ ...value, many: e.target.checked })} />
        As many as you like
      </label>
    </>
  )
}

export function itemFieldsToBody(v: { name: string; url: string; note: string; many: boolean }) {
  return { name: v.name.trim(), url: v.url.trim() || null, note: v.note.trim() || null, many: v.many }
}

interface Props {
  item: WishListItem
  manager: boolean
  /** The diary is read-only: managers cannot edit. Claims and releases still work. */
  readOnly: boolean
  published: boolean
  busy: boolean
  onClaim: () => void
  onUnclaim: () => void
  onRelease: (claimId: string) => void
  onSave: (fields: WishListItemFields) => Promise<void>
  onDelete: () => void
}

export default function WishListItemRow({
  item, manager, readOnly, published, busy, onClaim, onUnclaim, onRelease, onSave, onDelete,
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ name: '', url: '', note: '', many: false })

  function startEdit() {
    setDraft({ name: item.name, url: item.url ?? '', note: item.note ?? '', many: item.many })
    setEditing(true)
  }

  async function save(ev: React.FormEvent) {
    ev.preventDefault()
    await onSave(itemFieldsToBody(draft))
    setEditing(false)
  }

  if (editing) {
    return (
      <li>
        <form onSubmit={save} aria-label={`Edit ${item.name}`}>
          <WishListItemFormFields value={draft} onChange={setDraft} />
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="submit" className="btn btn-primary" disabled={busy || !draft.name.trim()}>Save item</button>
            <button type="button" className="btn btn-secondary" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      </li>
    )
  }

  const claims = item.claims ?? []
  return (
    <li style={{ marginBottom: '1rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>{item.name}</strong>
        {item.url && (
          <a href={item.url} target="_blank" rel="noopener noreferrer">See in store</a>
        )}
        {item.many && <span>As many as you like</span>}
      </div>
      {item.note && <p style={{ margin: '0.25rem 0' }}>{item.note}</p>}

      {published && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {item.mine ? (
            <>
              <span>You&apos;re getting this</span>
              <button type="button" className="btn btn-secondary" onClick={onUnclaim} disabled={busy}>Undo</button>
            </>
          ) : !item.taken ? (
            <button type="button" className="btn btn-secondary" onClick={onClaim} disabled={busy}>I&apos;m getting this</button>
          ) : !item.many && !manager ? (
            <span>Taken</span>
          ) : null}
          {item.many && item.claim_count > 0 && <span>{claimCountText(item.claim_count)}</span>}
        </div>
      )}

      {manager && claims.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0.25rem 0' }}>
          {claims.map((c) => (
            <li key={c.id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span>{c.name}</span>
              <button type="button" className="btn btn-secondary" onClick={() => onRelease(c.id)} disabled={busy}>
                Release
              </button>
            </li>
          ))}
        </ul>
      )}

      {manager && (
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" className="btn btn-secondary" aria-label={`Edit ${item.name}`} onClick={startEdit} disabled={busy || readOnly}>
            Edit
          </button>
          <button type="button" className="btn btn-secondary" aria-label={`Delete ${item.name}`} onClick={onDelete} disabled={busy || readOnly}>
            Delete
          </button>
        </div>
      )}
    </li>
  )
}
