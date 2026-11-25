import { fetchPublicPage } from '../publicApi'

const page = { kind: 'wish_list', wish_list: { title: 'T', list_date: '2026-03-14', pasted_list_url: null, shared_by: null, items: [] } }
const origEnv = process.env

beforeEach(() => {
  process.env = { ...origEnv, API_INTERNAL_URL: 'http://api:8000' }
  global.fetch = jest.fn().mockResolvedValue({ status: 200, ok: true, json: async () => page }) as unknown as typeof fetch
})
afterEach(() => {
  process.env = origEnv
})

describe('fetchPublicPage', () => {
  it('forwards the visitor address and returns null on 404', async () => {
    const got = await fetchPublicPage('tok/en', '203.0.113.9')
    expect(got).toEqual(page)
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0]
    expect(url).toBe('http://api:8000/v1/public/tok%2Fen')
    expect(init.cache).toBe('no-store')
    expect(init.headers['X-Forwarded-For']).toBe('203.0.113.9')

    ;(global.fetch as jest.Mock).mockResolvedValue({ status: 404, ok: false, json: async () => ({}) })
    expect(await fetchPublicPage('dead', null)).toBeNull()
    const [, init2] = (global.fetch as jest.Mock).mock.calls[1]
    expect(init2.headers['X-Forwarded-For']).toBeUndefined()
  })

  it('throws on other failures', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({ status: 500, ok: false, json: async () => ({}) })
    await expect(fetchPublicPage('boom', null)).rejects.toThrow()
  })
})
