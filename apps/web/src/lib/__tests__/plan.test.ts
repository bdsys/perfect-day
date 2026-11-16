import { ApiError } from '../api'
import {
  limitColor, formatResetDate, diaryLimitNote, limitMessage, readOnlyBanner,
  PHOTO_LIMIT_NOTE, BACKFILL_LIMIT_NOTE,
} from '../plan'

describe('limitColor', () => {
  it.each([
    [0, 5, 'green'],
    [1, 5, 'yellow'],
    [4, 5, 'yellow'],
    [5, 5, 'red'],
    [7, 5, 'red'],
    [0, 1, 'green'],
    [1, 1, 'red'],
  ])('used %i of %i is %s', (used, limit, color) => {
    expect(limitColor(used, limit)).toBe(color)
  })
})

describe('formatResetDate', () => {
  const originalTz = process.env.TZ
  afterEach(() => {
    if (originalTz === undefined) delete process.env.TZ
    else process.env.TZ = originalTz
  })

  it('reads the date as a calendar date, not shifted by the local zone', () => {
    process.env.TZ = 'America/Los_Angeles'
    // Guard: the zone really took effect, so the test would catch a local-time bug.
    expect(new Date('2026-12-01').getDate()).toBe(30)
    expect(formatResetDate('2026-12-01')).toBe('Dec 1')
  })
})

describe('diaryLimitNote', () => {
  it('singular and plural', () => {
    expect(diaryLimitNote('Free', 1)).toBe('Free plan: 1 diary.')
    expect(diaryLimitNote('Plus', 2)).toBe('Plus plan: 2 diaries.')
  })
})

describe('limitColor edge', () => {
  it('a zero limit is already red', () => {
    expect(limitColor(0, 0)).toBe('red')
  })
})

describe('null plan copy', () => {
  it('diaryLimitNote and readOnlyBanner fall back to "your plan"', () => {
    expect(diaryLimitNote(null, 1)).toBe('Your plan: 1 diary.')
    expect(diaryLimitNote(null, 2)).toBe('Your plan: 2 diaries.')
    expect(readOnlyBanner(null)).toBe(
      'This diary is read-only on your plan. Upgrade, or delete another diary, to use it again.',
    )
  })
})

describe('limitMessage', () => {
  const tl = (details: Record<string, unknown>) => new ApiError(403, 'tier_limit', 'tier_limit', details)

  it('is null for anything that is not a tier_limit ApiError', () => {
    expect(limitMessage(new Error('x'))).toBeNull()
    expect(limitMessage(new ApiError(500, 'boom'))).toBeNull()
    expect(limitMessage(new ApiError(403, 'nope', 'forbidden'))).toBeNull()
    expect(limitMessage('tier_limit')).toBeNull()
  })

  it('maps every source', () => {
    expect(limitMessage(tl({ source: 'ai_draft', resets_on: '2026-12-01' }))).toBe(
      'No AI drafts left this month. Resets Dec 1.',
    )
    expect(limitMessage(tl({ source: 'ai_draft' }), { plan_name: 'Free', resets_on: '2027-01-01' })).toBe(
      'No AI drafts left this month. Resets Jan 1.',
    )
    expect(limitMessage(tl({ source: 'ai_draft' }))).toBe('No AI drafts left this month.')
    expect(limitMessage(tl({ source: 'photo' }))).toBe(PHOTO_LIMIT_NOTE)
    expect(limitMessage(tl({ source: 'diary', limit: 1 }))).toBe('Your plan: 1 diary.')
    expect(limitMessage(tl({ source: 'diary', limit: 2 }), { plan_name: 'Plus', resets_on: '2026-12-01' })).toBe(
      'Plus plan: 2 diaries.',
    )
    expect(limitMessage(tl({ source: 'diary_read_only' }), { plan_name: 'Plus', resets_on: '2026-12-01' })).toBe(
      readOnlyBanner('Plus'),
    )
    expect(limitMessage(tl({ source: 'backfill' }))).toBe(BACKFILL_LIMIT_NOTE)
    expect(limitMessage(tl({ source: 'something_new' }))).toBe("Your plan doesn't include this.")
    expect(limitMessage(new ApiError(403, 'tier_limit', 'tier_limit'))).toBe("Your plan doesn't include this.")
  })

  it('BACKFILL_LIMIT_NOTE is the agreed copy', () => {
    expect(BACKFILL_LIMIT_NOTE).toBe('Backfill is on Plus and Family. Free scans look back 90 days.')
  })
})
