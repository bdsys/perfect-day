import { cache } from 'react'

// Server-side only (used from the /p/[token] server component). It calls the API inside
// Docker (API_INTERNAL_URL) and passes the visitor's address on, so the API's per-IP limit
// counts the visitor and not the web container.

export interface PublicItem {
  id: string
  name: string
  url: string | null
  note: string | null
  many: boolean
  taken: boolean
  claim_count: number
}

export interface PublicWishListData {
  title: string
  list_date: string
  pasted_list_url: string | null
  shared_by: string | null
  items: PublicItem[]
}

export type PublicPage = { kind: 'wish_list'; wish_list: PublicWishListData }

/** The API is busy (429), failing (5xx) or out of reach: the link may still be fine. */
export const UNAVAILABLE = 'unavailable'

/** The page, null for a dead link (404), or UNAVAILABLE. Never throws on a bad answer. */
export async function fetchPublicPageUncached(
  token: string,
  visitorIp: string | null,
): Promise<PublicPage | null | typeof UNAVAILABLE> {
  const base = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  const headers: Record<string, string> = {}
  if (visitorIp) headers['X-Forwarded-For'] = visitorIp
  let res: Response
  try {
    res = await fetch(`${base}/v1/public/${encodeURIComponent(token)}`, {
      cache: 'no-store',
      headers,
    })
  } catch {
    return UNAVAILABLE
  }
  if (res.status === 404) return null
  if (!res.ok) return UNAVAILABLE
  return (await res.json()) as PublicPage
}

/** One fetch per request, shared by generateMetadata and the page. */
export const fetchPublicPage = cache(fetchPublicPageUncached)
