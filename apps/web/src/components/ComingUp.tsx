'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api, type WishListSummary } from '@/lib/api'
import { formatListDate } from '@/lib/date'

/** Published wish lists whose date is close, above the entries. Renders nothing when there are none. */
export default function ComingUp({ diaryId }: { diaryId: string }) {
  const [lists, setLists] = useState<WishListSummary[]>([])

  useEffect(() => {
    let live = true
    api.wishLists
      .comingUp(diaryId)
      .then((r) => {
        if (live) setLists([...r.wish_lists].sort((a, b) => a.list_date.localeCompare(b.list_date)))
      })
      .catch(() => {
        // A side box: the diary works without it.
      })
    return () => {
      live = false
    }
  }, [diaryId])

  if (lists.length === 0) return null
  return (
    <section aria-label="Coming up" style={{ marginBottom: '1rem' }}>
      <h2>Coming up</h2>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {lists.map((l) => (
          <li key={l.id}>
            <Link href={`/diaries/${diaryId}/wish-lists/${l.id}`}>
              {`Wish list · ${l.title} · ${formatListDate(l.list_date)}`}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
