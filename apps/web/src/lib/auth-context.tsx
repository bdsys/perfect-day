'use client'

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import RestoreAccountScreen from '@/components/RestoreAccountScreen'
import { api, refreshAccessToken, setAccessToken, setPendingDeletionHandler, type UserProfile } from '@/lib/api'

interface AuthState {
  user: UserProfile | null
  loading: boolean
  /** Object URL of the profile photo; null unless the avatar is a photo */
  avatarUrl: string | null
  /** True when the account is within its deletion grace period */
  pendingDeletion: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName?: string) => Promise<void>
  logout: () => Promise<void>
  loginWithGoogle: (idToken: string) => Promise<void>
  refreshUser: () => Promise<void>
  restore: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  avatarUrl: null,
  pendingDeletion: false,
  login: async () => {},
  register: async () => {},
  logout: async () => {},
  loginWithGoogle: async () => {},
  refreshUser: async () => {},
  restore: async () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null)
  const [signalled, setSignalled] = useState(false)
  const urlRef = useRef<string | null>(null)

  const replaceAvatarUrl = useCallback((next: string | null) => {
    if (urlRef.current && urlRef.current !== next) URL.revokeObjectURL(urlRef.current)
    urlRef.current = next
    setAvatarUrl(next)
  }, [])

  // Bumped by every applyProfile, logout and unmount: a result whose token is no
  // longer current is stale and is dropped (including its object URL).
  const tokenRef = useRef(0)
  const invalidate = useCallback(() => {
    tokenRef.current++
  }, [])

  // Take a freshly read profile: store it and sync the photo's object URL. The user and
  // the URL are set together, after the blob arrives.
  const applyProfile = useCallback(
    async (me: UserProfile | null) => {
      const token = ++tokenRef.current
      let url: string | null = null
      if (me?.avatar?.kind === 'photo') {
        try {
          const blob = await api.auth.avatar()
          if (token !== tokenRef.current) return
          url = URL.createObjectURL(blob)
        } catch {
          if (token !== tokenRef.current) return
        }
      }
      setUser(me)
      replaceAvatarUrl(url)
    },
    [replaceAvatarUrl],
  )

  useEffect(() => {
    setPendingDeletionHandler(() => setSignalled(true))
    return () => {
      setPendingDeletionHandler(null)
      invalidate()
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [invalidate])

  useEffect(() => {
    // Try to refresh on mount (may have a valid cookie)
    refreshAccessToken()
      .then((ok) => (ok ? api.auth.me() : undefined))
      .then((me) => (me ? applyProfile(me) : undefined))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [applyProfile])

  const refreshUser = useCallback(async () => {
    await applyProfile(await api.auth.me())
  }, [applyProfile])

  const afterLogin = useCallback(async () => {
    setSignalled(false)
    await applyProfile(await api.auth.me())
  }, [applyProfile])

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await api.auth.login(email, password)
      setAccessToken(tokens.access_token)
      await afterLogin()
    },
    [afterLogin],
  )

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const tokens = await api.auth.register(email, password, displayName)
      setAccessToken(tokens.access_token)
      await afterLogin()
    },
    [afterLogin],
  )

  const logout = useCallback(async () => {
    invalidate()
    await api.auth.logout()
    setAccessToken('')
    setSignalled(false)
    await applyProfile(null)
  }, [applyProfile, invalidate])

  const loginWithGoogle = useCallback(
    async (idToken: string) => {
      const tokens = await api.auth.socialGoogle(idToken)
      setAccessToken(tokens.access_token)
      await afterLogin()
    },
    [afterLogin],
  )

  const restore = useCallback(async () => {
    await api.auth.restoreAccount()
    setSignalled(false)
    await refreshUser()
  }, [refreshUser])

  const pendingDeletion = signalled || !!user?.deletion_scheduled_for

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        avatarUrl,
        pendingDeletion,
        login,
        register,
        logout,
        loginWithGoogle,
        refreshUser,
        restore,
      }}
    >
      {pendingDeletion ? <RestoreAccountScreen /> : children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
