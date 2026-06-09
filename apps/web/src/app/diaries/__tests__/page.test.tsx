import { render, screen, waitFor } from '@testing-library/react'
import DiariesPage from '../page'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: {
        list: jest.fn().mockResolvedValue([]),
        create: jest.fn(),
      },
    },
  }
})

jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'a@b.com' },
    loading: false,
    logout: jest.fn(),
  }),
}))

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

// GoogleStatusBanner uses Suspense + useSearchParams; supress console errors
beforeEach(() => {
  jest.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  (console.error as jest.Mock).mockRestore()
})

describe('DiariesPage', () => {
  it('renders a "New diary" link pointing to /diaries/new', async () => {
    render(<DiariesPage />)
    await waitFor(() => {
      const link = screen.getByRole('link', { name: /new diary/i })
      expect(link).toHaveAttribute('href', '/diaries/new')
    })
  })

  it('does not render an inline create form', async () => {
    render(<DiariesPage />)
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /create a diary/i })).not.toBeInTheDocument()
    })
  })
})
