import { ApiError } from './api'

export type LimitColor = 'green' | 'yellow' | 'red'

/** green when nothing is used, red at or over the limit, yellow between. */
export function limitColor(used: number, limit: number): LimitColor {
  if (used >= limit) return 'red'
  if (used > 0) return 'yellow'
  return 'green'
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-12-01" -> "Dec 1". A calendar date, so the browser's zone never shifts it. */
export function formatResetDate(isoDate: string): string {
  const [, month, day] = isoDate.split('-').map(Number)
  return `${MONTHS[month - 1]} ${day}`
}

/** "Free plan: 1 diary." / "Plus plan: 2 diaries."; "Your plan: 1 diary." when the plan is unknown. */
export function diaryLimitNote(planName: string | null, n: number): string {
  return `${planName ? `${planName} plan` : 'Your plan'}: ${n} ${n === 1 ? 'diary' : 'diaries'}.`
}

/** Shown beside a grayed-out AI button when the month's drafts are used up. */
export function aiLimitNote(resetsOn: string): string {
  return `No AI drafts left this month. Resets ${formatResetDate(resetsOn)}.`
}

/** Shown above an entry whose generation was refused at the limit. */
export function aiEntryNote(resetsOn: string): string {
  return `Out of AI drafts until ${formatResetDate(resetsOn)}. Write it yourself, or generate it then.`
}

/** "Shared with 2 of 3 people"; with no limit "Shared with 2 people" / "Shared with 1 person". */
export function peopleCounter(used: number, limit: number | null): string {
  if (limit == null) return `Shared with ${used} ${used === 1 ? 'person' : 'people'}`
  return `Shared with ${used} of ${limit} people`
}

/** "Free plan: 3 people per diary." */
export function peopleLimitNote(planName: string | null, n: number): string {
  return `${planName ? `${planName} plan` : 'Your plan'}: ${n} ${n === 1 ? 'person' : 'people'} per diary.`
}

export const EDITOR_LIMIT_NOTE = 'Editors are on the Family plan.'

export const PHOTO_LIMIT_NOTE = 'Free plan: 1 photo per entry.'

export const BACKFILL_LIMIT_NOTE = 'Backfill is on Plus and Family. Free scans look back 90 days.'

export function readOnlyBanner(planName: string | null): string {
  return `This diary is read-only on ${planName ? `the ${planName} plan` : 'your plan'}. Upgrade, or delete another diary, to use it again.`
}

/**
 * Plain words for a 403 tier_limit refusal, or null when err is anything else
 * (so a caller can fall back to its own handling). plan is whatever the page
 * already knows; the refusal's own details win over it.
 */
export function limitMessage(
  err: unknown,
  plan?: { plan_name: string; resets_on: string } | null,
): string | null {
  if (!(err instanceof ApiError) || err.code !== 'tier_limit') return null
  const details = err.details ?? {}
  const planName = plan?.plan_name ?? null
  switch (details.source) {
    case 'ai_draft': {
      const resetsOn = typeof details.resets_on === 'string' ? details.resets_on : plan?.resets_on
      return resetsOn ? aiLimitNote(resetsOn) : 'No AI drafts left this month.'
    }
    case 'photo':
      return PHOTO_LIMIT_NOTE
    case 'diary':
      return diaryLimitNote(planName, typeof details.limit === 'number' ? details.limit : 1)
    case 'people':
      return peopleLimitNote(planName, typeof details.limit === 'number' ? details.limit : 0)
    case 'editor':
      return EDITOR_LIMIT_NOTE
    case 'diary_read_only':
      return readOnlyBanner(planName)
    case 'backfill':
      return BACKFILL_LIMIT_NOTE
    default:
      return "Your plan doesn't include this."
  }
}
