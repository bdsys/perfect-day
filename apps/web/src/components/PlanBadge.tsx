'use client'

import { useState } from 'react'
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
  const left = Math.max(0, limit - used)
  const text =
    `${label}: ${left} of ${limit} left` + (resetsOn ? ` · resets ${formatResetDate(resetsOn)}` : '')
  return (
    <span className="plan-badge-wrap">
      <button
        type="button"
        className="plan-badge"
        data-color={limitColor(used, limit)}
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
      >
        {text}
      </button>
      <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
      {open && details && details.length > 0 && (
        <ul className="plan-badge-details">
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
