import { safeNext } from '../nextPath'

describe('safeNext', () => {
  it('keeps a local path', () => {
    expect(safeNext('/invite/abc')).toBe('/invite/abc')
  })
  it.each(['//evil.example', 'https://evil.example', '/\\evil.example', null, ''])(
    'falls back for %p',
    (raw) => {
      expect(safeNext(raw)).toBe('/diaries')
    },
  )
})
