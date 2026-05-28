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
