export function formatDate(d: string): string {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Render an entry's date or date range. If endDate is null/undefined or
 * equal to startDate, returns just the formatted start date. Otherwise
 * returns "<start> – <end>" with an en-dash separator.
 */
export function formatDateRange(
  startDate: string,
  endDate: string | null | undefined,
): string {
  if (!endDate || endDate === startDate) {
    return formatDate(startDate)
  }
  return `${formatDate(startDate)} – ${formatDate(endDate)}`
}

/** An ISO timestamp as "Nov 29" in the browser's zone. */
export function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** A list's date ("2026-03-14") as "Sat 14 Mar". UTC, so the day never shifts with the browser's zone. */
export function formatListDate(isoDate: string): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  }).formatToParts(new Date(`${isoDate}T00:00:00Z`))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('weekday')} ${get('day')} ${get('month')}`
}
