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

export class ApiError extends Error {
  status: number
  code?: string
  details?: Record<string, unknown>
  constructor(
    status: number,
    message: string,
    code?: string,
    details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

async function apiFetchBlob(path: string, retryOn401 = true): Promise<Blob> {
  const headers: HeadersInit = {}
  if (_accessToken) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${_accessToken}`
  }
  const res = await fetch(`${API_BASE}${path}`, {
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
        return apiFetchBlob(path, false)
      }
    } catch {}
    // Refresh failed — clear token
    _accessToken = null
    throw new ApiError(401, 'unauthorized')
  }
  if (!res.ok) {
    let code: string | undefined
    let details: Record<string, unknown> | undefined
    let message: string
    try {
      const json = await res.json() as { detail?: unknown }
      const detail = json.detail
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const d = detail as Record<string, unknown>
        code = typeof d.code === 'string' ? d.code : undefined
        details = typeof d.details === 'object' && d.details !== null
          ? (d.details as Record<string, unknown>)
          : undefined
        message = code ?? JSON.stringify(detail)
      } else {
        message = typeof detail === 'string' ? detail : `API error ${res.status}`
      }
    } catch {
      message = `API error ${res.status}`
    }
    throw new ApiError(res.status, message, code, details)
  }
  return res.blob()
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
    let code: string | undefined
    let details: Record<string, unknown> | undefined
    let message: string
    try {
      const json = await res.json() as { detail?: unknown }
      const detail = json.detail
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const d = detail as Record<string, unknown>
        code = typeof d.code === 'string' ? d.code : undefined
        details = typeof d.details === 'object' && d.details !== null
          ? (d.details as Record<string, unknown>)
          : undefined
        message = code ?? JSON.stringify(detail)
      } else {
        message = typeof detail === 'string' ? detail : `HTTP ${res.status}`
      }
    } catch {
      message = `HTTP ${res.status}`
    }
    throw new ApiError(res.status, message, code, details)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type Photo = {
  id: string
  mime_type: string | null
  bytes: number | null
  taken_at: string | null
  lat: number | null
  lon: number | null
  source: string
  finalized_at: string | null
  created_at: string
  deleted_at: string | null
  has_thumbnail: boolean
}

export type UploadUrl = {
  photo_id: string
  upload_url: string
  upload_key: string
  expires_in: number
  required_headers: Record<string, string>
}

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
  hard_delete_after: string | null
  created_at: string
}

export interface Integration {
  provider: string
  scopes_granted: string[]
  revoked: boolean
  expires_at: string | null
  google_email: string | null
  google_name: string | null
}

export interface EventItem {
  id: string
  source: string
  occurred_at: string | null
  summary: string
  description: string | null
  location: string | null
  start: Record<string, string>
  end: Record<string, string>
  attendees: Array<{ displayName: string; email: string; organizer: boolean; responseStatus: string }>
  status: string
}

export interface CalendarEventSummary {
  id: string
  summary: string
  description: string
  location: string
  occurred_at: string | null
  start: Record<string, string>
  end: Record<string, string>
  attendees: Array<{ displayName?: string; email?: string; organizer?: boolean; responseStatus?: string }>
  status: string
}

export interface LLMGenerationSummary {
  id: string
  status: 'success' | 'failed'
  error: string | null
  created_at: string
  mode: 'events' | 'polish' | 'hybrid' | 'none'
  model: string | null
}

export interface Entry {
  id: string
  diary_id: string
  entry_date: string
  entry_end_date: string | null
  title: string | null
  body_markdown: string | null
  body_source: 'llm' | 'fallback' | 'llm_polished' | 'llm_hybrid'
  flagged_tokens: string[] | null
  status: 'draft' | 'published'
  created_by: 'auto' | 'manual'
  creation_source: 'manual' | 'calendar_pick' | 'rule' | 'legacy_auto'
  published_at: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
  events: EventItem[]
  rule_matches: RuleMatchSummary[]
  last_generation: LLMGenerationSummary | null
  photos: Photo[]
}

export interface ScanRun {
  id: string
  diary_id: string
  triggered_by: string
  started_at: string
  completed_at: string | null
  status: 'running' | 'success' | 'partial' | 'failed'
  events_calendar: number
  entries_created: number
  errors?: Array<{ source: string; message: string }> | null
}

export interface RuleConditionLeaf {
  field: 'title' | 'description' | 'location' | 'attendee_email'
  op: 'contains' | 'equals' | 'not_contains'
  value: string
  case_sensitive?: boolean
}

export interface RuleConditionGroup {
  op: 'AND' | 'OR'
  children: RuleCondition[]
}

export type RuleCondition = RuleConditionLeaf | RuleConditionGroup

export interface RuleOptions {
  recurring?: 'per_instance' | 'per_series'
  multi_day?: 'per_day' | 'spanning'
}

export interface Rule {
  id: string
  diary_id: string
  name: string
  condition: RuleCondition
  options: RuleOptions
  enabled: boolean
  last_applied_at: string | null
  created_at: string
  updated_at: string
}

export interface RuleCreate {
  name: string
  condition: RuleCondition
  options?: RuleOptions
  enabled?: boolean
}

export interface RulePreview {
  matched_count: number
  total_evaluated: number
  threshold_exceeded: boolean
  sample: Array<{ summary: string; occurred_at: string | null; location: string }>
}

export interface RuleMatchSummary {
  rule_id: string
  rule_name: string
  matched_at: string
}

export interface BackfillRun {
  id: string
  diary_id: string
  from_date: string
  to_date: string
  sources: string[]
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: string | null
  completed_at: string | null
  events_ingested: number
  entries_created: number
  error: string | null
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
    async socialGoogle(idToken: string): Promise<TokenPair> {
      return apiFetch('/v1/auth/social/google', {
        method: 'POST',
        body: JSON.stringify({ id_token: idToken }),
      })
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
    async triggerScan(
      id: string,
      options?: { past_days?: number; future_days?: number },
    ): Promise<{ queued: boolean; alreadyRunning: boolean }> {
      try {
        await apiFetch(`/v1/diaries/${id}/scan/run`, {
          method: 'POST',
          body: options ? JSON.stringify(options) : undefined,
        })
        return { queued: true, alreadyRunning: false }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : ''
        if (msg.includes('scan_in_progress') || msg.includes('409')) {
          return { queued: false, alreadyRunning: true }
        }
        throw e
      }
    },
    async listScanRuns(id: string): Promise<ScanRun[]> {
      return apiFetch(`/v1/diaries/${id}/scan/runs`)
    },
    async triggerBackfill(
      id: string,
      from_date: string,
      to_date: string,
    ): Promise<BackfillRun | { alreadyRunning: true }> {
      try {
        return await apiFetch<BackfillRun>(`/v1/diaries/${id}/scan/backfill`, {
          method: 'POST',
          body: JSON.stringify({ from_date, to_date, sources: ['google_calendar'] }),
        })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : ''
        if (msg.includes('scan_in_progress') || (e instanceof ApiError && e.status === 409)) {
          return { alreadyRunning: true }
        }
        throw e
      }
    },
    async getBackfillRun(id: string, runId: string): Promise<BackfillRun> {
      return apiFetch(`/v1/diaries/${id}/scan/backfill/${runId}`)
    },
    async cancelBackfillRun(id: string, runId: string): Promise<BackfillRun> {
      return apiFetch(`/v1/diaries/${id}/scan/backfill/${runId}`, { method: 'DELETE' })
    },
    async delete(id: string): Promise<Diary> {
      return apiFetch(`/v1/diaries/${id}`, { method: 'DELETE' })
    },
    async listTrash(): Promise<Diary[]> {
      return apiFetch('/v1/diaries/trash')
    },
    async restore(id: string): Promise<Diary> {
      return apiFetch(`/v1/diaries/${id}/restore`, { method: 'POST' })
    },
  },

  entries: {
    async list(diaryId: string, params: Record<string, string> = {}): Promise<Entry[]> {
      const q = new URLSearchParams(params).toString()
      return apiFetch(`/v1/diaries/${diaryId}/entries${q ? '?' + q : ''}`)
    },
    async create(
      diaryId: string,
      data: {
        entry_date: string
        entry_end_date?: string | null
        title?: string | null
        body_markdown?: string | null
      },
    ): Promise<Entry> {
      return apiFetch(`/v1/diaries/${diaryId}/entries`, { method: 'POST', body: JSON.stringify(data) })
    },
    async get(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}`)
    },
    async patch(id: string, data: Partial<Entry>): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
    },
    async delete(id: string): Promise<void> {
      return apiFetch(`/v1/entries/${id}`, { method: 'DELETE' })
    },
    async restore(id: string): Promise<Entry> {
      return apiFetch(`/v1/entries/${id}/restore`, { method: 'POST' })
    },
    async listTrash(diaryId: string): Promise<Entry[]> {
      return apiFetch(`/v1/diaries/${diaryId}/entries/trash`)
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
    async list(): Promise<Integration[]> {
      return apiFetch('/v1/integrations')
    },
  },

  calendarEvents: {
    async list(
      diaryId: string,
      params: { attached?: boolean; from?: string; to?: string; limit?: number } = {},
    ): Promise<CalendarEventSummary[]> {
      const q = new URLSearchParams()
      if (params.attached !== undefined) q.set('attached', String(params.attached))
      if (params.from) q.set('from', params.from)
      if (params.to) q.set('to', params.to)
      if (params.limit) q.set('limit', String(params.limit))
      const qs = q.toString()
      return apiFetch(`/v1/diaries/${diaryId}/calendar-events${qs ? '?' + qs : ''}`)
    },

    async createFromEvent(diaryId: string, eventId: string): Promise<Entry> {
      return apiFetch(`/v1/diaries/${diaryId}/entries/from-event`, {
        method: 'POST',
        body: JSON.stringify({ event_id: eventId }),
      })
    },
  },

  rules: {
    async list(diaryId: string): Promise<Rule[]> {
      return apiFetch(`/v1/diaries/${diaryId}/rules`)
    },

    async create(diaryId: string, body: RuleCreate): Promise<Rule> {
      return apiFetch(`/v1/diaries/${diaryId}/rules`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },

    async get(ruleId: string): Promise<Rule> {
      return apiFetch(`/v1/rules/${ruleId}`)
    },

    async patch(ruleId: string, body: Partial<RuleCreate>): Promise<Rule> {
      return apiFetch(`/v1/rules/${ruleId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
    },

    async delete(ruleId: string): Promise<void> {
      return apiFetch(`/v1/rules/${ruleId}`, { method: 'DELETE' })
    },

    async preview(diaryId: string, body: { condition: RuleCondition; options?: RuleOptions }): Promise<RulePreview> {
      return apiFetch(`/v1/diaries/${diaryId}/rules/preview`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },

    async apply(ruleId: string, days: number): Promise<{ queued: boolean }> {
      return apiFetch(`/v1/rules/${ruleId}/apply`, {
        method: 'POST',
        body: JSON.stringify({ days }),
      })
    },
  },

  photos: {
    requestUploadUrl: (body: { declared_mime: string; declared_size: number }) =>
      apiFetch<UploadUrl>('/v1/photos/upload-url', {
        method: 'POST',
        body: JSON.stringify(body),
      }),

    uploadFile: (uploadUrl: string, file: File, onProgress?: (p: number) => void): Promise<void> =>
      new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('PUT', uploadUrl)
        xhr.setRequestHeader('Content-Type', file.type)
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
        }
        xhr.onload = () =>
          xhr.status >= 200 && xhr.status < 300
            ? resolve()
            : reject(new Error(`upload failed: ${xhr.status}`))
        xhr.onerror = () => reject(new Error('upload network error'))
        xhr.send(file)
      }),

    finalize: (photoId: string) =>
      apiFetch<Photo>(`/v1/photos/${photoId}/finalize`, { method: 'POST' }),

    get: (photoId: string, kind: 'full' | 'thumb' = 'full'): Promise<Blob> =>
      apiFetchBlob(`/v1/photos/${photoId}?kind=${kind}`),

    delete: (photoId: string) =>
      apiFetch<void>(`/v1/photos/${photoId}`, { method: 'DELETE' }),

    listForUser: (): Promise<Photo[]> =>
      apiFetch<Photo[]>('/v1/photos'),

    attachToEntry: (entryId: string, photoId: string, position?: number) =>
      apiFetch<Photo>(`/v1/entries/${entryId}/photos`, {
        method: 'POST',
        body: JSON.stringify({ photo_id: photoId, position }),
      }),

    detachFromEntry: (entryId: string, photoId: string) =>
      apiFetch<void>(`/v1/entries/${entryId}/photos/${photoId}`, {
        method: 'DELETE',
      }),
  },
}
