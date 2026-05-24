const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export type TokenPair = { access_token: string }

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

let _accessToken: string | null = null

export function setAccessToken(token: string) {
  _accessToken = token
}

export function getAccessToken(): string | null {
  return _accessToken
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  retryOn401 = true,
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  }

  if (_accessToken) {
    ;(headers as Record<string, string>)['Authorization'] = `Bearer ${_accessToken}`
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include',
  })

  if (res.status === 401 && retryOn401) {
    // Try to refresh
    try {
      const refreshRes = await fetch(`${API_BASE}/v1/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (refreshRes.ok) {
        const data = (await refreshRes.json()) as TokenPair
        setAccessToken(data.access_token)
        return apiFetch<T>(path, options, false)
      }
    } catch {}
    // Refresh failed — clear token
    _accessToken = null
    throw new Error('unauthorized')
  }

  if (!res.ok) {
    const body = await res.text()
    throw new Error(body || `HTTP ${res.status}`)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Diary {
  id: string
  name: string
  slug: string
  timezone: string
  subject_name: string | null
  subject_relation: string
  scan_enabled: boolean
  scan_interval_minutes: number
  deleted_at: string | null
  created_at: string
}

export interface Entry {
  id: string
  diary_id: string
  entry_date: string
  entry_end_date: string | null
  title: string | null
  body_markdown: string | null
  flagged_tokens: string[] | null
  status: 'draft' | 'published'
  created_by: 'auto' | 'manual'
  published_at: string | null
  deleted_at: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const api = {
  auth: {
    async register(email: string, password: string, displayName?: string): Promise<TokenPair> {
      return apiFetch('/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password, display_name: displayName }),
      })
    },
    async login(email: string, password: string): Promise<TokenPair> {
      return apiFetch('/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
    },
    async logout(): Promise<void> {
      return apiFetch('/v1/auth/logout', { method: 'POST' })
    },
    async me() {
      return apiFetch('/v1/auth/me')
    },
  },

  diaries: {
    async list(): Promise<Diary[]> {
      return apiFetch('/v1/diaries')
    },
    async get(id: string): Promise<Diary> {
      return apiFetch(`/v1/diaries/${id}`)
    },
    async create(data: { name: string; timezone: string; subject_name?: string }): Promise<Diary> {
      return apiFetch('/v1/diaries', { method: 'POST', body: JSON.stringify(data) })
    },
    async triggerScan(id: string) {
      return apiFetch(`/v1/diaries/${id}/scan/run`, { method: 'POST' })
    },
  },

  entries: {
    async list(diaryId: string, params: Record<string, string> = {}): Promise<Entry[]> {
      const q = new URLSearchParams(params).toString()
      return apiFetch(`/v1/diaries/${diaryId}/entries${q ? '?' + q : ''}`)
    },
    async get(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}`)
    },
    async patch(id: string, data: Partial<Entry>): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
    },
    async publish(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}/publish`, { method: 'POST' })
    },
    async unpublish(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}/unpublish`, { method: 'POST' })
    },
    async regenerate(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}/regenerate`, { method: 'POST' })
    },
  },

  integrations: {
    async getGoogleAuthUrl(scopes = 'calendar'): Promise<{ url: string }> {
      return apiFetch(`/v1/integrations/google/authorize?scopes=${scopes}`)
    },
    async list() {
      return apiFetch('/v1/integrations')
    },
  },
}
