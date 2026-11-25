import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PublicPage, { generateMetadata } from '../[token]/page'
import PublicWishList from '../[token]/PublicWishList'

const mockFetchPublicPage = jest.fn()
jest.mock('@/lib/publicApi', () => ({ fetchPublicPage: (...a: unknown[]) => mockFetchPublicPage(...a) }))
jest.mock('next/headers', () => ({
  headers: async () => new Headers({ 'x-forwarded-for': '203.0.113.9, 10.0.0.1' }),
}))

const list = {
  title: "Emma's 6th birthday",
  list_date: '2026-03-14',
  pasted_list_url: 'https://www.toystore.example/lists/1',
  shared_by: 'Sam',
  items: [
    { id: 'i1', name: 'Lego set', url: 'https://shop.example/lego', note: 'Blue box', many: false, taken: false, claim_count: 0 },
    { id: 'i2', name: 'Books', url: null, note: null, many: true, taken: false, claim_count: 0 },
  ],
}
const data = { kind: 'wish_list', wish_list: list }
const params = Promise.resolve({ token: 'tok' })

beforeEach(() => {
  jest.clearAllMocks()
  localStorage.clear()
  global.fetch = jest.fn() as unknown as typeof fetch
})

function jsonRes(status: number, body: unknown) {
  return { status, ok: status < 400, json: async () => body }
}

describe('metadata', () => {
  it('is title and date only, noindex, no image', async () => {
    mockFetchPublicPage.mockResolvedValue(data)
    const m = await generateMetadata({ params })
    expect(m.title).toBe("Wish list: Emma's 6th birthday · Sat 14 Mar")
    expect(m.robots).toEqual({ index: false, follow: false })
    expect(m.referrer).toBe('no-referrer')
    expect(m.openGraph).toEqual({ title: "Wish list: Emma's 6th birthday · Sat 14 Mar" })
    // null (not undefined) resets the root layout's description in Next's merge.
    expect(m.description).toBeNull()
    expect(m.openGraph).not.toHaveProperty('description')
    expect(m.openGraph).not.toHaveProperty('images')
    expect(mockFetchPublicPage).toHaveBeenCalledWith('tok', '203.0.113.9')
  })

  it('is just Perfect Day for a dead link', async () => {
    mockFetchPublicPage.mockResolvedValue(null)
    const m = await generateMetadata({ params })
    expect(m.title).toBe('Perfect Day')
    expect(m.description).toBeNull()
  })
})

describe('the page', () => {
  it('dead link shows the inactive message', async () => {
    mockFetchPublicPage.mockResolvedValue(null)
    render(await PublicPage({ params }))
    expect(screen.getByText('This link is no longer active')).toBeInTheDocument()
    expect(screen.queryByText('Lego set')).not.toBeInTheDocument()
  })

  it('server-renders title, date, sharer, pasted list and items', async () => {
    mockFetchPublicPage.mockResolvedValue(data)
    render(await PublicPage({ params }))
    expect(screen.getByRole('heading', { name: "Emma's 6th birthday" })).toBeInTheDocument()
    expect(screen.getByText('Sat 14 Mar')).toBeInTheDocument()
    expect(screen.getByText('Shared by Sam')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: "Emma's 6th birthday list on toystore.example" })).toBeInTheDocument()
    expect(screen.getByText('Blue box')).toBeInTheDocument()
  })

  it('store links open with noopener noreferrer', async () => {
    mockFetchPublicPage.mockResolvedValue(data)
    render(await PublicPage({ params }))
    for (const a of screen.getAllByRole('link')) {
      expect(a).toHaveAttribute('target', '_blank')
      expect(a).toHaveAttribute('rel', 'noopener noreferrer')
    }
  })
})

describe('PublicWishList', () => {
  it("claim asks for a name, then shows You're getting this and Undo", async () => {
    const after = { ...data, wish_list: { ...list, items: [{ ...list.items[0], taken: true, claim_count: 1 }, list.items[1]] } }
    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce(jsonRes(201, { claim_id: 'c1', secret: 's1' }))
      .mockResolvedValueOnce(jsonRes(200, after))
    render(<PublicWishList token="tok" initial={list} />)
    fireEvent.click(screen.getAllByRole('button', { name: "I'm getting this" })[0])
    const input = screen.getByLabelText('Your name')
    expect(input).toBeRequired()
    expect(input).toHaveAttribute('maxLength', '60')
    fireEvent.change(input, { target: { value: 'Pat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText("You're getting this")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0]
    expect(url).toMatch(/\/v1\/public\/tok\/items\/i1\/claims$/)
    expect(JSON.parse(init.body)).toEqual({ name: 'Pat' })
    expect(JSON.parse(localStorage.getItem('pd-wish-claims')!)).toEqual({ i1: { claimId: 'c1', secret: 's1' } })
  })

  it('undo sends the secret and drops the remembered claim', async () => {
    localStorage.setItem('pd-wish-claims', JSON.stringify({ i1: { claimId: 'c1', secret: 's1' } }))
    const taken = { ...list, items: [{ ...list.items[0], taken: true, claim_count: 1 }, list.items[1]] }
    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce(jsonRes(204, null))
      .mockResolvedValueOnce(jsonRes(200, data))
    render(<PublicWishList token="tok" initial={taken} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.getAllByRole('button', { name: "I'm getting this" })).toHaveLength(2))
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0]
    expect(url).toMatch(/\/v1\/public\/tok\/claims\/c1$/)
    expect(init.method).toBe('DELETE')
    expect(init.headers['X-Claim-Secret']).toBe('s1')
    expect(JSON.parse(localStorage.getItem('pd-wish-claims')!)).toEqual({})
  })

  it('remembered claim survives reload; broken localStorage still renders', async () => {
    localStorage.setItem('pd-wish-claims', JSON.stringify({ i1: { claimId: 'c1', secret: 's1' } }))
    const taken = { ...list, items: [{ ...list.items[0], taken: true, claim_count: 1 }, list.items[1]] }
    const { unmount } = render(<PublicWishList token="tok" initial={taken} />)
    expect(await screen.findByText("You're getting this")).toBeInTheDocument()
    unmount()
    // not shown as yours when the server shows nobody on it
    const second = render(<PublicWishList token="tok" initial={list} />)
    expect(screen.queryByText("You're getting this")).not.toBeInTheDocument()
    second.unmount()

    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    render(<PublicWishList token="tok" initial={list} />)
    expect(screen.getByText('Lego set')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('shows plain copy for a taken item and an inactive link after a claim', async () => {
    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce(jsonRes(409, { error: { code: 'http_error', message: 'item_taken' } }))
      .mockResolvedValueOnce(jsonRes(200, data))
    render(<PublicWishList token="tok" initial={list} />)
    fireEvent.click(screen.getAllByRole('button', { name: "I'm getting this" })[0])
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Pat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Someone else just said they're getting this.")

    const dead = jsonRes(404, { error: { code: 'http_error', message: 'link_inactive' } })
    ;(global.fetch as jest.Mock).mockResolvedValueOnce(dead).mockResolvedValueOnce(dead)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.getByText('This link is no longer active')).toBeInTheDocument())
    expect(screen.queryByText('Lego set')).not.toBeInTheDocument()
  })

  it('shows rate-limit copy for a 429', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValueOnce(jsonRes(429, { error: { code: 'http_error', message: 'rate_limited' } }))
    render(<PublicWishList token="tok" initial={list} />)
    fireEvent.click(screen.getAllByRole('button', { name: "I'm getting this" })[0])
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Pat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many tries. Please wait a little and try again.')
  })

  it('claim link_inactive but the list still loads: item gone, not the dead view', async () => {
    const without = { ...data, wish_list: { ...list, items: [list.items[1]] } }
    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce(jsonRes(404, { error: { code: 'http_error', message: 'link_inactive' } }))
      .mockResolvedValueOnce(jsonRes(200, without))
    render(<PublicWishList token="tok" initial={list} />)
    fireEvent.click(screen.getAllByRole('button', { name: "I'm getting this" })[0])
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Pat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByText('Lego set')).not.toBeInTheDocument())
    expect(screen.getByText('Books')).toBeInTheDocument()
    expect(screen.queryByText('This link is no longer active')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })

  it('claim link_inactive and the list is 404: dead view', async () => {
    const dead = jsonRes(404, { error: { code: 'http_error', message: 'link_inactive' } })
    ;(global.fetch as jest.Mock).mockResolvedValueOnce(dead).mockResolvedValueOnce(dead)
    render(<PublicWishList token="tok" initial={list} />)
    fireEvent.click(screen.getAllByRole('button', { name: "I'm getting this" })[0])
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Pat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('This link is no longer active')).toBeInTheDocument()
  })
})
