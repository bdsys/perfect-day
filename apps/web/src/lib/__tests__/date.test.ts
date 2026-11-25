import { formatListDate } from '../date'

describe('formatListDate', () => {
  it('formatListDate keeps the day in Los Angeles', () => {
    // jest.global-setup.js runs every test in America/Los_Angeles, behind UTC.
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('America/Los_Angeles')
    expect(formatListDate('2026-03-14')).toBe('Sat 14 Mar')
    expect(formatListDate('2026-12-01')).toBe('Tue 1 Dec')
  })
})
