import { ApiError } from '@/lib/api'

/** Plain sentences for the API's wish-list reasons. Codes never reach the screen. */
export const WISH_LIST_ERROR_COPY: Record<string, string> = {
  item_taken: "Someone else just said they're getting this.",
  already_claimed: "You're already getting this.",
  item_claims_full: "This item can't take anyone else.",
  claim_not_found: 'That was already removed.',
  wish_list_full: 'This list has the most items it can hold (100).',
  wish_list_not_published: 'Publish the list first.',
}

/** The sentence for `e`: a known reason's copy (`extra` first), else `fallback`. */
export function wishListErrorMessage(
  e: unknown,
  fallback = 'Something went wrong',
  extra: Record<string, string> = {},
): string {
  if (e instanceof ApiError && e.code) return extra[e.code] ?? WISH_LIST_ERROR_COPY[e.code] ?? fallback
  return fallback
}
