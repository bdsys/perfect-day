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

/** "Free plan: 1 diary." / "Plus plan: 2 diaries." */
export function diaryLimitNote(planName: string, n: number): string {
  return `${planName} plan: ${n} ${n === 1 ? 'diary' : 'diaries'}.`
}

/** Shown beside a grayed-out AI button when the month's drafts are used up. */
export function aiLimitNote(resetsOn: string): string {
  return `No AI drafts left this month. Resets ${formatResetDate(resetsOn)}.`
}

/** Shown above an entry whose generation was refused at the limit. */
export function aiEntryNote(resetsOn: string): string {
  return `Out of AI drafts until ${formatResetDate(resetsOn)}. Write it yourself, or generate it then.`
}

export const PHOTO_LIMIT_NOTE = 'Free plan: 1 photo per entry.'

export function readOnlyBanner(planName: string): string {
  return `This diary is read-only on the ${planName} plan. Upgrade, or delete another diary, to use it again.`
}
