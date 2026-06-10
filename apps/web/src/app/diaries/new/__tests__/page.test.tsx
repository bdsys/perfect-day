import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import NewDiaryPage from '../page'
import { api, ApiError } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: {
        create: jest.fn(),
        get: jest.fn(),
        list: jest.fn(),
        patch: jest.fn(),
      },
    },
  }
})
jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false }),
}))

const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
}))

beforeEach(() => {
  jest.clearAllMocks()
})

describe('NewDiaryPage', () => {
  it('renders the diary form', () => {
    render(<NewDiaryPage />)
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create diary/i })).toBeInTheDocument()
  })

  it('calls api.diaries.create with form values and redirects on success', async () => {
    ;(api.diaries.create as jest.Mock).mockResolvedValue({ id: 'diary-1', name: "Emma's diary" })
    render(<NewDiaryPage />)

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: "Emma's diary" } })
    fireEvent.click(screen.getByRole('button', { name: /my child/i }))
    fireEvent.click(screen.getByRole('button', { name: /create diary/i }))

    await waitFor(() => expect(api.diaries.create).toHaveBeenCalledTimes(1))
    expect(api.diaries.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Emma's diary", subject_relation: 'child' })
    )
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/diaries/diary-1'))
  })

  it('shows a tier_limit error message when the diary limit is reached', async () => {
    const err = new ApiError(403, 'Diary limit reached', 'tier_limit', { limit: 1, current: 1, source: 'diaries' })
    ;(api.diaries.create as jest.Mock).mockRejectedValue(err)
    render(<NewDiaryPage />)

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New diary' } })
    fireEvent.click(screen.getByRole('button', { name: /create diary/i }))

    await waitFor(() => expect(screen.getByText(/diary limit/i)).toBeInTheDocument())
    expect(mockPush).not.toHaveBeenCalled()
  })

  it('shows a generic error message on other API failures', async () => {
    ;(api.diaries.create as jest.Mock).mockRejectedValue(new Error('Server error'))
    render(<NewDiaryPage />)

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New diary' } })
    fireEvent.click(screen.getByRole('button', { name: /create diary/i }))

    await waitFor(() => expect(screen.getByText(/server error/i)).toBeInTheDocument())
  })

  it('has a back link to /diaries', () => {
    render(<NewDiaryPage />)
    const back = screen.getByRole('link', { name: /diaries/i })
    expect(back).toHaveAttribute('href', '/diaries')
  })
})
