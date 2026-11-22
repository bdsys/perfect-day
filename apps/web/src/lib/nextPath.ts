const BASE = 'http://x.invalid'

/** Where to go after sign-in: the `next` value only if it stays on this site. */
export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/')) return '/diaries'
  // Control characters (URL parsing drops tab/LF/CR) and backslashes can hide a host.
  if (/[\u0000- \u007f\\]/.test(raw)) return '/diaries'
  try {
    const url = new URL(raw, BASE)
    if (url.origin !== BASE) return '/diaries'
    const out = url.pathname + url.search + url.hash
    // Dot segments can normalise to //host, which the router treats as external.
    return /^\/(?!\/)/.test(out) ? out : '/diaries'
  } catch {
    return '/diaries'
  }
}
