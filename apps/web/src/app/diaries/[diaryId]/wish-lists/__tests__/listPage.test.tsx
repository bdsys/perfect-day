import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import WishListPage from '../[listId]/page'
import { api, ApiError, type WishList, type WishListItem } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: { get: jest.fn() },
      auth: { updateProfile: jest.fn() },
      wishLists: {
        get: jest.fn(),
        update: jest.fn(),
        remove: jest.fn(),
        addItem: jest.fn(),
        updateItem: jest.fn(),
        removeItem: jest.fn(),
        publish: jest.fn(),
        unpublish: jest.fn(),
        linkOff: jest.fn(),
        newLink: jest.fn(),
        claim: jest.fn(),
        unclaim: jest.fn(),
        release: jest.fn(),
      },
    },
  }
})

jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})

jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() }
  return { useRouter: () => router, useParams: () => ({ diaryId: 'diary-1', listId: 'list-1' }) }
})

const item = (over: Partial<WishListItem> = {}): WishListItem => ({
  id: 'i1', name: 'Lego set', url: null, note: null, many: false,
  taken: false, claim_count: 0, mine: false, claims: null, ...over,
})
const list = (over: Partial<WishList> = {}): WishList => ({
  id: 'list-1', diary_id: 'diary-1', title: "Emma's 6th birthday", list_date: '2026-03-14',
  pasted_list_url: null, status: 'draft', items: [item()], link: null, ...over,
})
const diary = (over: object = {}) => ({
  id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Andrew', plan: { read_only: false, plan_name: 'Free' }, ...over,
})

function setup(l: WishList, d: object = diary()) {
  ;(api.diaries.get as jest.Mock).mockResolvedValue(d)
  ;(api.wishLists.get as jest.Mock).mockResolvedValue(l)
}

beforeEach(() => {
  jest.resetAllMocks()
})

describe('WishListPage', () => {
  it('manager sees claims with names; viewer sees Taken without names', async () => {
    const claimed = list({
      status: 'published',
      items: [
        item({ taken: true, claim_count: 1, claims: [{ id: 'c1', name: 'Gran', signed_in: false, created_at: 'x' }] }),
      ],
    })
    setup(claimed)
    const { unmount } = render(<WishListPage />)
    expect(await screen.findByText('Gran')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Release' })).toBeInTheDocument()
    unmount()

    setup(
      list({ status: 'published', items: [item({ taken: true, claim_count: 1, claims: null })] }),
      diary({ role: 'viewer', plan: null }),
    )
    render(<WishListPage />)
    expect(await screen.findByText('Taken')).toBeInTheDocument()
    expect(screen.queryByText('Gran')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Release' })).not.toBeInTheDocument()
  })

  it('shows the counts copy for items many people can take', async () => {
    setup(
      list({
        status: 'published',
        items: [
          item({ id: 'a', name: 'Card', many: true, taken: true, claim_count: 1 }),
          item({ id: 'b', name: 'Book', many: true, taken: true, claim_count: 3 }),
        ],
      }),
      diary({ role: 'viewer', plan: null }),
    )
    render(<WishListPage />)
    expect(await screen.findByText('1 person is getting this')).toBeInTheDocument()
    expect(screen.getByText('3 people are getting this')).toBeInTheDocument()
  })

  it('shows the pasted list line with a safe link', async () => {
    setup(list({ pasted_list_url: 'https://www.amazon.com/hz/wishlist/ls/ABC' }))
    render(<WishListPage />)
    const a = await screen.findByRole('link', { name: "Emma's 6th birthday list on amazon.com" })
    expect(a).toHaveAttribute('href', 'https://www.amazon.com/hz/wishlist/ls/ABC')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
    expect(a).toHaveAttribute('target', '_blank')
  })

  it('publish without a name asks for it and retries', async () => {
    setup(list())
    ;(api.wishLists.publish as jest.Mock)
      .mockRejectedValueOnce(new ApiError(409, 'display_name_required', 'display_name_required', { caller_is_owner: true }))
      .mockResolvedValueOnce(list({ status: 'published', link: { token: 't', path: '/p/t' } }))
    ;(api.auth.updateProfile as jest.Mock).mockResolvedValue({})
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Publish' }))
    const field = await screen.findByLabelText('Your name, as people with the link will see it')
    fireEvent.change(field, { target: { value: 'Andrew' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save and publish' }))
    await waitFor(() => expect(api.auth.updateProfile).toHaveBeenCalledWith({ display_name: 'Andrew' }))
    await waitFor(() => expect(api.wishLists.publish).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: 'Unpublish' })).toBeInTheDocument()
  })

  it("tells an editor to ask the owner when the owner has no name", async () => {
    setup(list(), diary({ role: 'editor', owner_name: 'Sam' }))
    ;(api.wishLists.publish as jest.Mock).mockRejectedValue(
      new ApiError(409, 'display_name_required', 'display_name_required', { caller_is_owner: false }),
    )
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Publish' }))
    expect(await screen.findByText('Ask Sam to publish this list. Perfect Day will ask them for their name.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Your name, as people with the link will see it')).not.toBeInTheDocument()
  })

  it('copy link copies the /p/ URL', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    setup(list({ status: 'published', link: { token: 'tok', path: '/p/tok' } }))
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Copy link' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/p/tok`))
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('turns the link off and makes a new one', async () => {
    const published = list({ status: 'published', link: { token: 'tok', path: '/p/tok' } })
    setup(published)
    ;(api.wishLists.get as jest.Mock)
      .mockResolvedValueOnce(published)
      .mockResolvedValueOnce({ ...published, link: null })
      .mockResolvedValueOnce({ ...published, link: { token: 'n', path: '/p/n' } })
    ;(api.wishLists.linkOff as jest.Mock).mockResolvedValue(undefined)
    ;(api.wishLists.newLink as jest.Mock).mockResolvedValue(undefined)
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn link off' }))
    expect(await screen.findByText(/The link is off/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Wish list link')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Make a new link' }))
    await waitFor(() => expect(api.wishLists.newLink).toHaveBeenCalledWith('list-1'))
    expect(await screen.findByDisplayValue(`${window.location.origin}/p/n`)).toBeInTheDocument()
  })

  it('viewer can claim and undo', async () => {
    const open = list({ status: 'published', items: [item()] })
    const mine = list({ status: 'published', items: [item({ taken: true, claim_count: 1, mine: true })] })
    setup(open, diary({ role: 'viewer', plan: null }))
    ;(api.wishLists.get as jest.Mock).mockResolvedValueOnce(open).mockResolvedValueOnce(mine).mockResolvedValueOnce(open)
    ;(api.wishLists.claim as jest.Mock).mockResolvedValue(mine.items[0])
    ;(api.wishLists.unclaim as jest.Mock).mockResolvedValue(open.items[0])
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: "I'm getting this" }))
    await waitFor(() => expect(api.wishLists.claim).toHaveBeenCalledWith('i1'))
    expect(await screen.findByText("You're getting this")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.wishLists.unclaim).toHaveBeenCalledWith('i1'))
    expect(await screen.findByRole('button', { name: "I'm getting this" })).toBeInTheDocument()
  })

  it('manager releases a claim', async () => {
    setup(
      list({
        status: 'published',
        items: [item({ taken: true, claim_count: 1, claims: [{ id: 'c1', name: 'Gran', signed_in: false, created_at: 'x' }] })],
      }),
    )
    ;(api.wishLists.release as jest.Mock).mockResolvedValue(undefined)
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Release' }))
    await waitFor(() => expect(api.wishLists.release).toHaveBeenCalledWith('c1'))
  })

  it('read-only diary disables editing but not Turn link off', async () => {
    setup(
      list({ status: 'published', link: { token: 'tok', path: '/p/tok' } }),
      diary({ plan: { read_only: true, plan_name: 'Free' } }),
    )
    render(<WishListPage />)
    expect(await screen.findByText(/This diary is read-only/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save details' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add item' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Make a new link' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit Lego set' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Turn link off' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Unpublish' })).toBeEnabled()
  })

  it('adds an item', async () => {
    setup(list())
    ;(api.wishLists.addItem as jest.Mock).mockResolvedValue(item({ id: 'i2', name: 'Bike' }))
    render(<WishListPage />)
    const form = (await screen.findByRole('form', { name: 'Add item' }))
    fireEvent.change(within(form).getByLabelText('Item'), { target: { value: 'Bike' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add item' }))
    await waitFor(() =>
      expect(api.wishLists.addItem).toHaveBeenCalledWith('list-1', { name: 'Bike', url: null, note: null, many: false }),
    )
  })

  it('a failed claim shows a plain sentence and reloads the list', async () => {
    const open = list({ status: 'published', items: [item()] })
    const taken = list({ status: 'published', items: [item({ taken: true, claim_count: 1 })] })
    setup(open, diary({ role: 'viewer', plan: null }))
    ;(api.wishLists.get as jest.Mock).mockResolvedValueOnce(open).mockResolvedValueOnce(taken)
    ;(api.wishLists.claim as jest.Mock).mockRejectedValue(new ApiError(409, 'item_taken', 'item_taken'))
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: "I'm getting this" }))
    expect(await screen.findByText("Someone else just said they're getting this.")).toBeInTheDocument()
    expect(screen.queryByText('item_taken')).not.toBeInTheDocument()
    expect(await screen.findByText('Taken')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "I'm getting this" })).not.toBeInTheDocument()
  })

  it('a failed release shows a plain sentence and reloads the list', async () => {
    const claimed = list({
      status: 'published',
      items: [item({ taken: true, claim_count: 1, claims: [{ id: 'c1', name: 'Gran', signed_in: false, created_at: 'x' }] })],
    })
    setup(claimed)
    ;(api.wishLists.get as jest.Mock).mockResolvedValueOnce(claimed).mockResolvedValueOnce(list({ status: 'published' }))
    ;(api.wishLists.release as jest.Mock).mockRejectedValue(new ApiError(404, 'claim_not_found', 'claim_not_found'))
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Release' }))
    expect(await screen.findByText('That was already removed.')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Gran')).not.toBeInTheDocument())
  })

  it('an unknown error shows the generic message, not the code', async () => {
    setup(list({ status: 'published', items: [item()] }), diary({ role: 'viewer', plan: null }))
    ;(api.wishLists.claim as jest.Mock).mockRejectedValue(new ApiError(500, 'weird_code', 'weird_code'))
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: "I'm getting this" }))
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument()
    expect(screen.queryByText('weird_code')).not.toBeInTheDocument()
  })

  it('a failed item save keeps the form open with the typed text', async () => {
    setup(list())
    ;(api.wishLists.updateItem as jest.Mock).mockRejectedValue(new ApiError(422, 'invalid', 'invalid'))
    render(<WishListPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Lego set' }))
    const form = screen.getByRole('form', { name: 'Edit Lego set' })
    fireEvent.change(within(form).getByLabelText('Item'), { target: { value: 'Lego castle' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save item' }))
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByRole('form', { name: 'Edit Lego set' })).toBeInTheDocument()
    expect(screen.getByDisplayValue('Lego castle')).toBeInTheDocument()
  })
})
