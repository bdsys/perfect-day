'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import GoogleSignInButton from '@/components/GoogleSignInButton'

export default function LoginPage() {
  const { user, loading: authLoading, login, loginWithGoogle } = useAuth()
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!authLoading && user) {
      router.replace('/diaries')
    }
  }, [authLoading, user, router])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      router.push('/diaries')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleGoogleCredential(idToken: string) {
    setError('')
    setLoading(true)
    try {
      await loginWithGoogle(idToken)
      router.push('/diaries')
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setError('An account with this email exists. Sign in with email and password instead.')
      } else if (err instanceof ApiError && err.status === 401) {
        setError('This account is unavailable.')
      } else {
        setError('Google sign-in failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  if (authLoading || user) {
    return <div className="loading">Loading…</div>
  }

  return (
    <div className="container" style={{ maxWidth: 400, paddingTop: '4rem' }}>
      <div className="card">
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '1.5rem' }}>Sign in</h1>
        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label" htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          {error && <p className="error-message">{error}</p>}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading}
            style={{ width: '100%', marginTop: '0.5rem', justifyContent: 'center' }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', margin: '1rem 0' }}>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>or</span>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        </div>
        <GoogleSignInButton onCredential={handleGoogleCredential} />
        <p style={{ marginTop: '1rem', fontSize: '0.875rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          No account? <Link href="/register">Register</Link>
        </p>
      </div>
    </div>
  )
}
