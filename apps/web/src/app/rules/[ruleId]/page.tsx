'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Rule, type RuleCondition, type RuleOptions } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { RuleForm } from '@/components/RuleForm'

function EditRulePageInner() {
  const { ruleId } = useParams<{ ruleId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [rule, setRule] = useState<Rule | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !ruleId) return
    api.rules.get(ruleId)
      .then(setRule)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load rule'))
      .finally(() => setLoading(false))
  }, [user, ruleId])

  async function handleSave(name: string, condition: RuleCondition, options: RuleOptions) {
    if (!rule) return
    setSaving(true)
    setError('')
    try {
      await api.rules.patch(rule.id, { name, condition, options })
      router.push(`/diaries/${rule.diary_id}/rules`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save rule')
      setSaving(false)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null
  if (!rule) return <div className="container" style={{ paddingTop: '1.5rem' }}><p className="error-message">{error || 'Rule not found.'}</p></div>

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${rule.diary_id}/rules`} className="nav-brand">← Rules</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <h1 className="page-title">Edit Rule</h1>
        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        <RuleForm
          diaryId={rule.diary_id}
          initialName={rule.name}
          initialCondition={rule.condition}
          initialOptions={rule.options}
          saving={saving}
          onSave={handleSave}
        />
      </div>
    </>
  )
}

export default function EditRulePage() {
  return (
    <Suspense fallback={<div className="loading">Loading…</div>}>
      <EditRulePageInner />
    </Suspense>
  )
}
