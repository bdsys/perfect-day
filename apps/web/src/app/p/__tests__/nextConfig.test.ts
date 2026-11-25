// eslint-disable-next-line @typescript-eslint/no-require-imports
const config = require('../../../../next.config.js')

describe('next.config headers', () => {
  it('cover /p/ with the three privacy headers', async () => {
    const rules = await config.headers()
    const rule = rules.find((r: { source: string }) => r.source === '/p/:path*')
    expect(rule).toBeDefined()
    const h = Object.fromEntries(rule.headers.map((x: { key: string; value: string }) => [x.key, x.value]))
    expect(h).toEqual({
      'X-Robots-Tag': 'noindex, nofollow',
      'Referrer-Policy': 'no-referrer',
      'Cache-Control': 'no-store',
    })
  })
})
