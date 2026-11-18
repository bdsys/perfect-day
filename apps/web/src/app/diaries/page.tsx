'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { api, type Diary, type MyPlan } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { googleStatusMessage } from '@/lib/googleStatus'
import AppHeader from '@/components/AppHeader'
import PlanBadge from '@/components/PlanBadge'
import LimitNote from '@/components/LimitNote'
import { diaryLimitNote, formatResetDate } from '@/lib/plan'

function GoogleStatusBanner() {
  const searchParams = useSearchParams()
  const msg = googleStatusMessage({
    status: searchParams.get('google'),
    missing: searchParams.get('missing'),
    reason: searchParams.get('reason'),
    expected: searchParams.get('expected'),
  })
  if (!msg) return null
  return (
    <p className={msg.isError ? 'error-message' : 'success-message'} style={{ marginBottom: '1rem' }}>
      {msg.text}
    </p>
  )
}

/** Every limit on the person's own plan, shown when the badge is tapped. */
function planDetails(plan: MyPlan) {
  const details = [
    { label: 'Diaries', text: `${Math.max(0, plan.diaries_limit - plan.diaries_used)} of ${plan.diaries_limit} left` },
  ]
  if (plan.ai_drafts_limit != null) {
    const left = plan.ai_drafts_left ?? Math.max(0, plan.ai_drafts_limit - plan.ai_drafts_used)
    details.push({
      label: 'AI drafts',
      text: `${left} of ${plan.ai_drafts_limit} left · resets ${formatResetDate(plan.resets_on)}`,
    })
  }
  details.push({
    label: 'Photos per entry',
    text: plan.photos_per_entry == null ? 'Unlimited' : String(plan.photos_per_entry),
  })
  return details
}

export default function DiariesPage() {
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diaries, setDiaries] = useState<Diary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<MyPlan | null>(null)

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace('/login')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    if (user) {
      api.diaries.list()
        .then(setDiaries)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false))
      // The page works without the plan; it only adds the badge and the gray-out.
      api.auth.myPlan().then(setPlan).catch(() => {})
    }
  }, [user])

  const atLimit = plan != null && plan.diaries_used >= plan.diaries_limit

  if (authLoading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <AppHeader />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <div className="page-header">
          <h1 className="page-title">Your diaries</h1>
          <div className="page-actions">
            {atLimit ? (
              <button type="button" className="btn btn-primary" disabled aria-describedby="diary-limit-note">New diary</button>
            ) : (
              <Link href="/diaries/new" className="btn btn-primary">New diary</Link>
            )}
            <Link href="/photos" className="btn btn-secondary">Photos</Link>
            <Link href="/diaries/restore" className="btn btn-secondary">Deleted diaries</Link>
          </div>
        </div>

        {plan?.tier === 'free' && (
          <div style={{ marginBottom: '0.75rem' }}>
            <PlanBadge label="Diaries" used={plan.diaries_used} limit={plan.diaries_limit} details={planDetails(plan)} />
          </div>
        )}
        {atLimit && plan && <LimitNote id="diary-limit-note">{diaryLimitNote(plan.plan_name, plan.diaries_limit)}</LimitNote>}

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        <Suspense>
          <GoogleStatusBanner />
        </Suspense>

        {loading ? (
          <div className="loading">Loading…</div>
        ) : diaries.length === 0 ? (
          <div className="empty-state">
            <p>No diaries yet. Create your first one below.</p>
          </div>
        ) : (
          <div>
            {diaries.map((d) => (
              <Link key={d.id} href={`/diaries/${d.id}`} style={{ display: 'block', marginBottom: '0.75rem', color: 'inherit', textDecoration: 'none' }}>
                <div className="card" style={{ cursor: 'pointer', transition: 'border-color 0.15s' }}>
                  <div style={{ fontWeight: 600, fontSize: '1rem' }}>{d.name}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                    {d.timezone} · Scan every {d.scan_interval_minutes} min
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}

      </div>
    </>
  )
}
