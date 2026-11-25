import type { Metadata } from 'next'
import { headers } from 'next/headers'
import { fetchPublicPage } from '@/lib/publicApi'
import { formatListDate } from '@/lib/date'
import PublicWishList from './PublicWishList'

// Never cached: a claim must show up for the next visitor straight away.
export const dynamic = 'force-dynamic'

type Props = { params: Promise<{ token: string }> }

/** Caddy sets X-Forwarded-For to exactly the visitor's address. */
async function visitorIp(): Promise<string | null> {
  const xff = (await headers()).get('x-forwarded-for')
  return xff?.split(',')[0]?.trim() || null
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { token } = await params
  const page = await fetchPublicPage(token, await visitorIp())
  // Chat apps fetch this from their own servers: title and date only, no image.
  const title = page
    ? `Wish list: ${page.wish_list.title} · ${formatListDate(page.wish_list.list_date)}`
    : 'Perfect Day'
  return {
    title,
    // null clears the root layout's description, so a preview shows title and date only.
    description: null,
    robots: { index: false, follow: false },
    referrer: 'no-referrer',
    openGraph: { title },
  }
}

export default async function PublicWishListPage({ params }: Props) {
  const { token } = await params
  const page = await fetchPublicPage(token, await visitorIp())
  return <PublicWishList token={token} initial={page?.wish_list ?? null} />
}
