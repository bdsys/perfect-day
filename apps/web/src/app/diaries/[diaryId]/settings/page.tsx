'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Diary } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { DiaryForm, type DiaryFormValues } from '@/components/DiaryForm'

export default function DiarySettingsPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [scanEnabled, setScanEnabled] = useState(true)
  const [scanToggling, setScanToggling] = useState(false)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.diaries.get(diaryId)
      .then((d) => {
        setDiary(d)
        setScanEnabled(d.scan_enabled)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [user, diaryId])

  async function handleSave(values: DiaryFormValues) {
    if (!diary) return
    setSaving(true)
    setError('')
    try {
      await api.diaries.patch(diary.id, values)
      router.push(`/diaries/${diary.id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save settings')
      setSaving(false)
    }
  }

  async function handleScanToggle(enabled: boolean) {
    if (!diary) return
    setScanEnabled(enabled)
    setScanToggling(true)
    try {
      await api.diaries.patch(diary.id, { scan_enabled: enabled })
    } catch (e: unknown) {
      // Revert on failure
      setScanEnabled(!enabled)
      setError(e instanceof Error ? e.message : 'Failed to update scan setting')
    } finally {
      setScanToggling(false)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user || !diary) return null

  const initialValues: Partial<DiaryFormValues> = {
    name: diary.name,
    subject_relation: diary.subject_relation ?? null,
    subject_name: diary.subject_name,
    tone_hint: diary.tone_hint,
    voice_override: diary.voice_override,
    scan_interval_minutes: diary.scan_interval_minutes,
    timezone: diary.timezone,
  }

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>

      <div className="page-container">
        <div className="page-header">
          <h1 className="page-title">Diary settings</h1>
        </div>

        {error && (
          <div className="error-message" style={{ marginBottom: '1rem' }}>{error}</div>
        )}

        {/* Scan-enabled toggle — edit-only, not in shared DiaryForm */}
        <div className="card" style={{ marginBottom: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontWeight: 600 }}>Auto-scan</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>
                Automatically scan for new calendar events and generate draft entries.
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                aria-label="Scan enabled"
                checked={scanEnabled}
                disabled={scanToggling}
                onChange={(e) => handleScanToggle(e.target.checked)}
              />
              <span style={{ fontSize: '0.9rem' }}>{scanEnabled ? 'Enabled' : 'Disabled'}</span>
            </label>
          </div>
        </div>

        <div className="card">
          <DiaryForm mode="edit" initialValues={initialValues} saving={saving} onSave={handleSave} />
        </div>
      </div>
    </>
  )
}
