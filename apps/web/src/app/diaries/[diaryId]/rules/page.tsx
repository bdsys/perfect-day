'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { api, type Rule } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

export default function RulesListPage() {
  const { diaryId } = useParams<{ diaryId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()

  const [rules, setRules] = useState<Rule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toggling, setToggling] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [applying, setApplying] = useState<string | null>(null)
  const [applyDays, setApplyDays] = useState<Record<string, number>>({})
  const [applySuccess, setApplySuccess] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user || !diaryId) return
    api.rules.list(diaryId)
      .then(setRules)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load rules'))
      .finally(() => setLoading(false))
  }, [user, diaryId])

  async function handleToggle(rule: Rule) {
    setToggling(rule.id)
    try {
      const updated = await api.rules.patch(rule.id, { enabled: !rule.enabled })
      setRules(prev => prev.map(r => r.id === rule.id ? updated : r))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to update rule')
    } finally {
      setToggling(null)
    }
  }

  async function handleDelete(rule: Rule) {
    if (!confirm(`Delete rule "${rule.name}"? This cannot be undone.`)) return
    setDeleting(rule.id)
    try {
      await api.rules.delete(rule.id)
      setRules(prev => prev.filter(r => r.id !== rule.id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete rule')
    } finally {
      setDeleting(null)
    }
  }

  async function handleApply(rule: Rule) {
    const days = applyDays[rule.id] ?? 30
    setApplying(rule.id)
    setApplySuccess(null)
    try {
      await api.rules.apply(rule.id, days)
      setApplySuccess(rule.id)
      setTimeout(() => setApplySuccess(null), 4000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to apply rule')
    } finally {
      setApplying(null)
    }
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href={`/diaries/${diaryId}`} className="nav-brand">← Diary</Link>
        </div>
      </nav>
      <div className="container" style={{ paddingTop: '1.5rem', maxWidth: 720 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
          <h1 className="page-title" style={{ margin: 0 }}>Auto-Creation Rules</h1>
          <button className="btn btn-primary" onClick={() => router.push(`/diaries/${diaryId}/rules/new`)}>
            + New Rule
          </button>
        </div>

        <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
          Rules automatically create diary entries when matching calendar events are synced. Each rule is evaluated against new events at scan time.
        </p>

        {error && <p className="error-message" style={{ marginBottom: '1rem' }}>{error}</p>}

        {rules.length === 0 ? (
          <div className="empty-state">
            <p>No rules yet. <Link href={`/diaries/${diaryId}/rules/new`}>Create your first rule</Link> to automatically capture events that match your criteria.</p>
          </div>
        ) : (
          <div>
            {rules.map(rule => (
              <div key={rule.id} className="card" style={{ marginBottom: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      <span style={{ fontWeight: 600, fontSize: '1rem' }}>{rule.name}</span>
                      <span style={{
                        fontSize: '0.75rem',
                        padding: '0.2rem 0.5rem',
                        borderRadius: 4,
                        background: rule.enabled ? '#dcfce7' : '#f3f4f6',
                        color: rule.enabled ? '#166534' : '#6b7280',
                        fontWeight: 500,
                      }}>
                        {rule.enabled ? 'Active' : 'Disabled'}
                      </span>
                    </div>
                    {rule.last_applied_at && (
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                        Last applied: {new Date(rule.last_applied_at).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
                    <button
                      className="btn btn-secondary"
                      style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
                      onClick={() => handleToggle(rule)}
                      disabled={toggling === rule.id}
                    >
                      {toggling === rule.id ? '…' : rule.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      className="btn btn-secondary"
                      style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
                      onClick={() => router.push(`/rules/${rule.id}`)}
                    >
                      Edit
                    </button>
                    <button
                      className="btn btn-danger"
                      style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
                      onClick={() => handleDelete(rule)}
                      disabled={deleting === rule.id}
                    >
                      {deleting === rule.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                </div>

                {/* Apply to past N days */}
                <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem', borderTop: '1px solid #eee', paddingTop: '0.75rem' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Apply to past</span>
                  <select
                    value={applyDays[rule.id] ?? 30}
                    onChange={(e) => setApplyDays(prev => ({ ...prev, [rule.id]: Number(e.target.value) }))}
                    style={{ fontSize: '0.85rem', padding: '0.2rem 0.4rem' }}
                  >
                    <option value={7}>7 days</option>
                    <option value={30}>30 days</option>
                    <option value={90}>90 days</option>
                  </select>
                  <button
                    className="btn btn-secondary"
                    style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
                    onClick={() => handleApply(rule)}
                    disabled={applying === rule.id}
                  >
                    {applying === rule.id ? 'Queuing…' : 'Apply'}
                  </button>
                  {applySuccess === rule.id && (
                    <span style={{ fontSize: '0.8rem', color: '#166534' }}>Queued ✓</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
