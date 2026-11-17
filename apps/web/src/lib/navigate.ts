/** Full-page navigation (kept in one place so tests can replace it). */
export function navigateTo(url: string): void {
  window.location.assign(url)
}

/** Replace the current page, leaving no history entry to come Back to (kept here so tests can replace it). */
export function replaceLocation(url: string): void {
  window.location.replace(url)
}
