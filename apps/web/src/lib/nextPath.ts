/** Where to go after sign-in: the `next` value only if it is a path on this site. */
export function safeNext(raw: string | null): string {
  if (raw && raw.startsWith('/') && raw[1] !== '/' && raw[1] !== '\\') return raw
  return '/diaries'
}
