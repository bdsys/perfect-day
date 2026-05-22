'use client'

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, setAccessToken } from '@/lib/api'

interface AuthState {
  user: Record<string, unknown> | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Try to refresh on mount (may have a valid cookie)
    fetch(`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/v1/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.access_token) {
          setAccessToken(data.access_token)
          return api.auth.me()
        }
      })
      .then((me) => {
        if (me) setUser(me as Record<string, unknown>)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api.auth.login(email, password)
    setAccessToken(tokens.access_token)
    const me = await api.auth.me()
    setUser(me as Record<string, unknown>)
  }, [])

  const logout = useCallback(async () => {
    await api.auth.logout()
    setAccessToken('')
    setUser(null)
  }, [])

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
