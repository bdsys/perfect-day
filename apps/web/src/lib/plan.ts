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
