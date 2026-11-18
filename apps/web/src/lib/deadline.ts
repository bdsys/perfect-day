/** A deadline as the exact local moment, e.g. "Nov 24 at 9:04 AM", in the browser's zone. */
export function formatDeadline(iso: string): string {
  const d = new Date(iso)
  const date = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(d)
  const time = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(d)
  return `${date} at ${time}`
}
