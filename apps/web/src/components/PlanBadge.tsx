'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { limitColor, formatResetDate } from '@/lib/plan'

interface Props {
  label: string
  used: number
  limit: number
  resetsOn?: string | null
  details?: { label: string; text: string }[]
}

/** "AI drafts: 3 of 5 left · resets Dec 1", coloured by usage, with an upgrade link. */
export default function PlanBadge({ label, used, limit, resetsOn, details }: Props) {
  const [open, setOpen] = useState(false)
  const listId = useId()
  const hasDetails = details != null && details.length > 0
  const left = Math.max(0, limit - used)
  const text =
    `${label}: ${left} of ${limit} left` + (resetsOn ? ` · resets ${formatResetDate(resetsOn)}` : '')
  return (
    <span className="plan-badge-wrap">
      <button
        type="button"
        className="plan-badge"
        data-color={limitColor(used, limit)}
        aria-expanded={hasDetails ? open : undefined}
        aria-controls={hasDetails ? listId : undefined}
        onClick={hasDetails ? () => setOpen(o => !o) : undefined}
      >
        {text}
      </button>
      <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
      {open && hasDetails && (
        <ul id={listId} className="plan-badge-details">
          {details.map(d => (
            <li key={d.label}>
              <strong>{d.label}</strong> <span>{d.text}</span>
            </li>
          ))}
        </ul>
      )}
    </span>
  )
}
