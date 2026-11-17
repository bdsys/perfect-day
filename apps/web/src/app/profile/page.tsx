'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import AppHeader from '@/components/AppHeader'
import Avatar from '@/components/Avatar'
import {
  api, ApiError, type AvatarChoice, type AvatarIconName, type Integration, type TemperatureUnit,
} from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { AVATAR_ICONS, TONES } from '@/lib/avatarIcons'
import { navigateTo } from '@/lib/navigate'

const ICON_ERROR = "Couldn't save your icon. Try again."

function iconErrorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 413) return 'That photo is too large.'
  if (e instanceof ApiError && e.status === 415) return "That file isn't a photo we can use."
  return ICON_ERROR
}

export default function ProfilePage() {
  const { user, loading: authLoading, avatarUrl, refreshUser, logout } = useAuth()
  const router = useRouter()
  const [planName, setPlanName] = useState('')
  const [avatar, setAvatar] = useState<AvatarChoice | undefined>(undefined)
  const [unit, setUnit] = useState<TemperatureUnit>('F')
  const [busy, setBusy] = useState<string | null>(null)
  const [iconError, setIconError] = useState('')
  const [unitError, setUnitError] = useState('')
  const [google, setGoogle] = useState<Integration | null>(null)
  const [googleLoaded, setGoogleLoaded] = useState(false)
  const [googleError, setGoogleError] = useState('')
  const [sessionError, setSessionError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [deleteError, setDeleteError] = useState('')

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [authLoading, user, router])

  useEffect(() => {
    if (user) {
      setAvatar(user.avatar)
      setUnit(user.temperature_unit)
    }
  }, [user])

  useEffect(() => {
    if (!user) return
    api.auth.myPlan().then((p) => setPlanName(p.plan_name)).catch(() => {})
    loadGoogle()
  }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  async function loadGoogle() {
    try {
      const list = await api.integrations.list()
      setGoogle(list.find((i) => i.provider === 'google' && !i.revoked) ?? null)
    } catch {
      setGoogleError("Couldn't load your Google connection.")
    } finally {
      setGoogleLoaded(true)
    }
  }

  async function saveIcon(name: AvatarIconName | null) {
    if (busy) return
    setBusy('icon')
    setIconError('')
    try {
      await api.auth.updateProfile({ avatar_icon: name })
      setAvatar(name ? { kind: 'icon', name } : { kind: 'initial' })
      await refreshUser()
    } catch (e) {
      setIconError(iconErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function upload(file: File) {
    setBusy('icon')
    setIconError('')
    try {
      await api.auth.uploadAvatar(file)
      setAvatar({ kind: 'photo' })
      await refreshUser()
    } catch (e) {
      setIconError(iconErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function saveUnit(next: TemperatureUnit) {
    if (busy) return
    setBusy('unit')
    setUnitError('')
    try {
      await api.auth.updateProfile({ temperature_unit: next })
      setUnit(next)
      await refreshUser()
    } catch {
      setUnitError("Couldn't save your temperature setting. Try again.")
    } finally {
      setBusy(null)
    }
  }

  async function connectGoogle() {
    if (busy) return
    setBusy('google')
    setGoogleError('')
    try {
      const { url } = await api.integrations.getGoogleAuthUrl('calendar', '/profile')
      navigateTo(url)
    } catch {
      setGoogleError("Couldn't start the Google connection. Try again.")
      setBusy(null)
    }
  }

  async function disconnectGoogle() {
    if (busy) return
    setBusy('google')
    setGoogleError('')
    try {
      await api.integrations.disconnectGoogle()
      await loadGoogle()
    } catch {
      setGoogleError("Couldn't disconnect Google. Try again.")
    } finally {
      setBusy(null)
    }
  }

  async function logOutEverywhere() {
    if (busy) return
    setBusy('sessions')
    setSessionError('')
    try {
      await api.auth.logoutAll()
    } catch {
      setSessionError("Couldn't log you out everywhere. Try again.")
      setBusy(null)
      return
    }
    try {
      await logout()
    } catch {
      // the server already revoked every session
    }
    navigateTo('/login')
  }

  async function deleteAccount() {
    if (busy || confirmText !== 'DELETE') return
    setBusy('delete')
    setDeleteError('')
    try {
      const { deletion_scheduled_for } = await api.auth.deleteAccount()
      try {
        await logout()
      } catch {
        // the server already revoked every session
      }
      navigateTo('/login?deleted=' + encodeURIComponent(deletion_scheduled_for))
    } catch {
      setDeleteError("Couldn't delete your account. Try again.")
      setBusy(null)
    }
  }

  if (authLoading || !user) return <div className="loading">Loading…</div>

  const chosenIcon = avatar?.kind === 'icon' ? avatar.name : null
  const iconBusy = busy === 'icon'

  return (
    <>
      <AppHeader back={{ href: '/diaries', label: '← Diaries' }} />
      <div className="page-container">
        <div className="page-header">
          <h1 className="page-title">Profile</h1>
        </div>

        <section className="card profile-section" aria-labelledby="who-h">
          <h2 id="who-h">Account</h2>
          <p>Signed in as {user.email}</p>
          <p>
            Plan: <span>{planName}</span> · <Link href="/plans">See plans</Link>
          </p>
        </section>

        <section className="card profile-section" aria-labelledby="icon-h">
          <h2 id="icon-h">Your icon</h2>
          <div className="profile-row">
            <Avatar avatar={avatar} email={user.email} photoUrl={avatarUrl ?? null} size={72} />
            <label className="btn btn-secondary upload-label" htmlFor="avatar-file">
              Upload a photo
            </label>
            <input
              id="avatar-file"
              className="visually-hidden"
              type="file"
              accept="image/*"
              disabled={iconBusy}
              onChange={(e) => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) void upload(file)
              }}
            />
            <button type="button" className="btn btn-secondary" disabled={iconBusy} onClick={() => saveIcon(null)}>
              Use my initial
            </button>
          </div>
          <div className="icon-grid">
            {AVATAR_ICONS.map(({ name, Icon, tone }) => (
              <button
                key={name}
                type="button"
                className="icon-choice"
                aria-label={name}
                aria-pressed={chosenIcon === name}
                disabled={iconBusy}
                onClick={() => saveIcon(name)}
              >
                <span
                  className="avatar"
                  style={{ width: 44, height: 44, backgroundColor: TONES[tone].bg, color: TONES[tone].fg }}
                >
                  <Icon aria-hidden="true" size={24} />
                </span>
              </button>
            ))}
          </div>
          {iconError && <p className="error-message" role="alert">{iconError}</p>}
        </section>

        <section className="card profile-section" aria-labelledby="temp-h">
          <h2 id="temp-h">Temperature</h2>
          <div role="radiogroup" aria-labelledby="temp-h" className="profile-row">
            {(['F', 'C'] as const).map((u) => (
              <label key={u} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <input
                  type="radio"
                  name="temperature-unit"
                  checked={unit === u}
                  disabled={busy === 'unit'}
                  onChange={() => saveUnit(u)}
                />
                {`° ${u}`}
              </label>
            ))}
          </div>
          <p className="profile-helper">Weather on your entries and in new AI drafts.</p>
          {unitError && <p className="error-message" role="alert">{unitError}</p>}
        </section>

        <section className="card profile-section" aria-labelledby="google-h">
          <h2 id="google-h">Google</h2>
          {googleLoaded && (
            <div className="profile-row">
              {google ? (
                <>
                  <span>Connected{google.google_email ? ` as ${google.google_email}` : ''}</span>
                  <button type="button" className="btn btn-secondary" disabled={busy === 'google'} onClick={disconnectGoogle}>
                    Disconnect Google
                  </button>
                </>
              ) : (
                <>
                  <span>Not connected</span>
                  <button type="button" className="btn btn-primary" disabled={busy === 'google'} onClick={connectGoogle}>
                    Connect Google
                  </button>
                </>
              )}
            </div>
          )}
          {googleError && <p className="error-message" role="alert">{googleError}</p>}
        </section>

        <section className="card profile-section" aria-labelledby="sessions-h">
          <h2 id="sessions-h">Sessions</h2>
          <button type="button" className="btn btn-secondary" disabled={busy === 'sessions'} onClick={logOutEverywhere}>
            Log out on all devices
          </button>
          {sessionError && <p className="error-message" role="alert">{sessionError}</p>}
        </section>

        <section className="card profile-section" aria-labelledby="delete-h">
          <h2 id="delete-h">Delete account</h2>
          <p>Your diaries, entries and photos are deleted after 7 days. Log in during those 7 days to undo it.</p>
          {!confirming ? (
            <button type="button" className="btn btn-danger" style={{ marginTop: '0.75rem' }} onClick={() => setConfirming(true)}>
              Delete account…
            </button>
          ) : (
            <div style={{ marginTop: '0.75rem' }}>
              <label className="form-label" htmlFor="confirm-delete">Type DELETE to confirm</label>
              <input
                id="confirm-delete"
                type="text"
                value={confirmText}
                autoComplete="off"
                onChange={(e) => setConfirmText(e.target.value)}
              />
              <div className="profile-row" style={{ marginTop: '0.75rem' }}>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={confirmText !== 'DELETE' || busy === 'delete'}
                  onClick={deleteAccount}
                >
                  Delete my account
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy === 'delete'}
                  onClick={() => { setConfirming(false); setConfirmText(''); setDeleteError('') }}
                >
                  Cancel
                </button>
              </div>
              {deleteError && <p className="error-message" role="alert">{deleteError}</p>}
            </div>
          )}
        </section>
      </div>
    </>
  )
}
