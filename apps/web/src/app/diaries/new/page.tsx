'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { DiaryForm, type DiaryFormValues } from '@/components/DiaryForm'

export default function NewDiaryPage() {
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [tierLimitError, setTierLimitError] = useState(false)

  if (authLoading) return <div className="loading">Loading…</div>
  if (!user) {
    router.replace('/login')
    return null
  }

  async function handleCreate(values: DiaryFormValues) {
    setSaving(true)
    setError('')
    setTierLimitError(false)
    try {
      const diary = await api.diaries.create(values)
      router.push(`/diaries/${diary.id}`)
    } catch (e: unknown) {
      if (e instanceof ApiError && e.code === 'tier_limit') {
        setTierLimitError(true)
      } else {
        setError(e instanceof Error ? e.message : 'Failed to create diary')
      }
      setSaving(false)
    }
  }

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href="/diaries" className="nav-brand">← Diaries</Link>
        </div>
      </nav>

      <div className="page-container">
        <div className="page-header">
          <h1 className="page-title">New diary</h1>
        </div>

        {tierLimitError && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>
            You&rsquo;ve reached your diary limit. Upgrade your plan to create more diaries.
          </div>
        )}
        {error && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>{error}</div>
        )}

        <div className="card">
          <DiaryForm mode="create" saving={saving} onSave={handleCreate} />
        </div>
      </div>
    </>
  )
}
