import { render, screen, waitFor } from '@testing-library/react'
import DiariesPage from '../page'
import { api, type MyPlan } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: {
        list: jest.fn().mockResolvedValue([]),
        create: jest.fn(),
      },
      auth: { myPlan: jest.fn() },
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

const plusPlan = {
  tier: 'plus', plan_name: 'Plus', diaries_limit: 2, diaries_used: 0, ai_drafts_limit: null, ai_drafts_used: 0,
  ai_drafts_left: null, resets_on: '2026-12-01', photos_per_entry: null, read_only_diary_ids: [],
} as MyPlan

// GoogleStatusBanner uses Suspense + useSearchParams; supress console errors
beforeEach(() => {
  ;(api.auth.myPlan as jest.Mock).mockResolvedValue(plusPlan)
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

describe('DiariesPage plan', () => {
  it('Free at the limit: red badge, disabled New diary with the diary note', async () => {
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({
      ...plusPlan, tier: 'free', plan_name: 'Free', diaries_limit: 1, diaries_used: 1,
      ai_drafts_limit: 5, ai_drafts_left: 5, photos_per_entry: 1,
    })
    render(<DiariesPage />)
    const badge = await screen.findByRole('button', { name: 'Diaries: 0 of 1 left' })
    expect(badge).toHaveAttribute('data-color', 'red')
    const btn = screen.getByRole('button', { name: 'New diary' })
    expect(btn).toBeDisabled()
    expect(document.getElementById(btn.getAttribute('aria-describedby')!)).toHaveTextContent('Free plan: 1 diary.')
    expect(screen.queryByRole('link', { name: /new diary/i })).not.toBeInTheDocument()
  })

  it('Plus below the limit shows no badge and a New diary link', async () => {
    render(<DiariesPage />)
    expect(await screen.findByRole('link', { name: /new diary/i })).toBeInTheDocument()
    expect(screen.queryByText(/Diaries:/)).not.toBeInTheDocument()
  })

  it('Plus at the limit has no badge but the button is disabled with the note', async () => {
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({ ...plusPlan, diaries_used: 2 })
    render(<DiariesPage />)
    expect(await screen.findByRole('button', { name: 'New diary' })).toBeDisabled()
    expect(screen.getByText('Plus plan: 2 diaries.')).toBeInTheDocument()
    expect(screen.queryByText(/Diaries:/)).not.toBeInTheDocument()
  })
})
