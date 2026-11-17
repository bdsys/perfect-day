'use client'

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import Link from 'next/link'
import Avatar from '@/components/Avatar'
import { useAuth } from '@/lib/auth-context'
import { navigateTo } from '@/lib/navigate'

interface Props {
  back?: { href: string; label: string }
}

/** The one header on every signed-in page: brand, the page's back link, and the account menu. */
export default function AppHeader({ back }: Props) {
  const { user, avatarUrl, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const profileRef = useRef<HTMLAnchorElement>(null)
  const signOutRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => {
    setOpen(false)
    buttonRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    profileRef.current?.focus()
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') close()
    }
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open, close])

  function onMenuKey(e: KeyboardEvent) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const onProfile = document.activeElement === profileRef.current
    ;(onProfile ? signOutRef : profileRef).current?.focus()
  }

  async function onSignOut() {
    setOpen(false)
    await logout()
    navigateTo('/login')
  }

  return (
    <nav className="app-header">
      <div className="app-header-inner">
        <div className="app-header-left">
          <Link href="/diaries" className="app-header-brand">Perfect Day</Link>
          {back && <Link href={back.href} className="app-header-back">{back.label}</Link>}
        </div>
        {user && (
          <div className="app-header-account" ref={wrapRef}>
            <button
              ref={buttonRef}
              type="button"
              className="avatar-btn"
              aria-haspopup="menu"
              aria-expanded={open}
              aria-label="Account menu"
              onClick={() => setOpen((o) => !o)}
            >
              <Avatar avatar={user.avatar} email={user.email} photoUrl={avatarUrl ?? null} size={34} />
            </button>
            {open && (
              <div className="app-menu" role="menu" onKeyDown={onMenuKey}>
                <Link ref={profileRef} href="/profile" role="menuitem" className="app-menu-item" onClick={() => setOpen(false)}>
                  Profile
                </Link>
                <button ref={signOutRef} type="button" role="menuitem" className="app-menu-item" onClick={onSignOut}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </nav>
  )
}
