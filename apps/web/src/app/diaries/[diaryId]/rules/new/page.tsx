'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type RuleCondition, type RuleOptions } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { RuleForm } from '@/components/RuleForm'

export default function NewRulePage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user } = useAuth()
  const router = useRouter()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  if (!user) return null

  async function handleSave(name: string, condition: RuleCondition, options: RuleOptions) {
    setSaving(true)
    setError('')
    try {
      await api.rules.create(diaryId, { name, condition, options })
      router.push(`/diaries/${diaryId}/rules`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save rule')
      setSaving(false)
    }
  }

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}/rules`} className="nav-brand">← Rules</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <h1 className="page-title">New Auto-Creation Rule</h1>
        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}
        <RuleForm diaryId={diaryId} saving={saving} onSave={handleSave} />
      </div>
    </>
  )
}
