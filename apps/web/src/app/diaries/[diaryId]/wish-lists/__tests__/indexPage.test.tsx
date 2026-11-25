import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import WishListsPage from '../page'
import { api, ApiError } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { diaries: { get: jest.fn() }, wishLists: { list: jest.fn(), create: jest.fn() } } }
})
jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})
const mockPush = jest.fn()
jest.mock('next/navigation', () => {
  const router = { push: (p: string) => mockPush(p), replace: jest.fn() }
  return { useRouter: () => router, useParams: () => ({ diaryId: 'diary-1' }) }
})

const SUMMARY = { id: 'l1', diary_id: 'diary-1', title: "Emma's birthday", list_date: '2026-03-14', status: 'published', item_count: 3 }

beforeEach(() => {
  jest.resetAllMocks()
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'diary-1', name: 'Emma diary', role: 'owner', plan: { read_only: false } })
  ;(api.wishLists.list as jest.Mock).mockResolvedValue([SUMMARY])
})

describe('WishListsPage', () => {
  it('lists the wish lists, linking to each', async () => {
    render(<WishListsPage />)
    const a = await screen.findByRole('link', { name: /Emma's birthday/ })
    expect(a).toHaveAttribute('href', '/diaries/diary-1/wish-lists/l1')
    expect(a).toHaveTextContent('Sat 14 Mar')
  })

  it('managers can make a new wish list', async () => {
    ;(api.wishLists.create as jest.Mock).mockResolvedValue({ ...SUMMARY, id: 'new1' })
    render(<WishListsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'New wish list' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Christmas' } })
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-12-25' } })
    fireEvent.click(screen.getByRole('button', { name: 'Make list' }))
    await waitFor(() =>
      expect(api.wishLists.create).toHaveBeenCalledWith('diary-1', { title: 'Christmas', list_date: '2026-12-25', pasted_list_url: null }),
    )
    expect(mockPush).toHaveBeenCalledWith('/diaries/diary-1/wish-lists/new1')
  })

  it('viewers get no New wish list button', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'diary-1', name: 'Emma diary', role: 'viewer', plan: null })
    render(<WishListsPage />)
    await screen.findByRole('link', { name: /Emma's birthday/ })
    expect(screen.queryByRole('button', { name: 'New wish list' })).not.toBeInTheDocument()
  })

  it('disables New wish list on a read-only diary', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'diary-1', name: 'Emma diary', role: 'owner', plan: { read_only: true, plan_name: 'Free' } })
    render(<WishListsPage />)
    expect(await screen.findByRole('button', { name: 'New wish list' })).toBeDisabled()
  })

  it('a diary that is gone says so in words', async () => {
    ;(api.diaries.get as jest.Mock).mockRejectedValue(new ApiError(404, 'not_found', 'not_found'))
    render(<WishListsPage />)
    expect(await screen.findByText("This diary isn't available any more.")).toBeInTheDocument()
    expect(screen.queryByText('not_found')).not.toBeInTheDocument()
  })

  it('a failed create shows the generic message, not the code', async () => {
    ;(api.wishLists.create as jest.Mock).mockRejectedValue(new ApiError(422, 'validation_error', 'validation_error'))
    render(<WishListsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'New wish list' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Christmas' } })
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-12-25' } })
    fireEvent.click(screen.getByRole('button', { name: 'Make list' }))
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument()
    expect(screen.queryByText('validation_error')).not.toBeInTheDocument()
  })
})
