/** Full-page navigation (kept in one place so tests can replace it). */
export function navigateTo(url: string): void {
  window.location.assign(url)
}
