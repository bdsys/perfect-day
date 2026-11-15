import { limitColor, formatResetDate, diaryLimitNote } from '../plan'

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
