import { safeNext } from '../nextPath'

describe('safeNext', () => {
  it.each(['/invite/abc', '/invite/abc?x=1#y'])('keeps %p', (raw) => {
    expect(safeNext(raw)).toBe(raw)
  })
  it('resolves dot segments that stay on-site', () => {
    expect(safeNext('/a/../invite/tok')).toBe('/invite/tok')
  })
  it('keeps an encoded double slash on-site, as resolved', () => {
    expect(safeNext('/%2F%2Fevil.example')).toBe('/%2F%2Fevil.example')
  })
  it.each([
    '//evil.example',
    'https://evil.example',
    '/\\evil.example',
    '/\t/evil.example',
    '/\n/evil.example',
    '/\r/evil.example',
    '/\t\\evil.example',
    '/..//evil.example',
    '/.//evil.example',
    '/a/..//evil.example',
    '/%2e%2e//evil.example',
    null,
    '',
  ])('falls back for %p', (raw) => {
    expect(safeNext(raw)).toBe('/diaries')
  })
})
