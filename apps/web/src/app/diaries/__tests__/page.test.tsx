import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
  backfill: true, scan_past_days_max: null, people_per_diary: 3, editors: false,
} as MyPlan

// GoogleStatusBanner uses Suspense + useSearchParams; supress console errors
beforeEach(() => {
  ;(api.auth.myPlan as jest.Mock).mockResolvedValue(plusPlan)
  jest.spyOn(console, 'error').mockImplementation(() => {})
})
beforeEach(() => { (api.diaries.list as jest.Mock).mockResolvedValue([]) })
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

  it('tapping the Free badge shows every limit', async () => {
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({
      ...plusPlan, tier: 'free', plan_name: 'Free', diaries_limit: 1, diaries_used: 0,
      ai_drafts_limit: 5, ai_drafts_used: 2, ai_drafts_left: 3, photos_per_entry: 1,
    })
    render(<DiariesPage />)
    const badge = await screen.findByRole('button', { name: 'Diaries: 1 of 1 left' })
    expect(badge).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(badge)
    const items = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(items).toEqual([
      'Diaries 1 of 1 left',
      'AI drafts 3 of 5 left · resets Dec 1',
      'Photos per entry 1',
      'People per diary 3',
    ])
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

const base = {
  slug: 's', subject_name: null, subject_relation: 'child', voice_override: null, tone_hint: '', scan_enabled: true,
  lat: null, lon: null, location_name: null, deleted_at: null, hard_delete_after: null, created_at: '2026-01-01',
  timezone: 'America/Los_Angeles', scan_interval_minutes: 60,
}

describe('DiariesPage sharing', () => {
  it('shows no creation prompts while the list is still loading', async () => {
    ;(api.diaries.list as jest.Mock).mockReturnValue(new Promise(() => {}))
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({ ...plusPlan, tier: 'free', plan_name: 'Free', diaries_limit: 1, diaries_used: 0 })
    render(<DiariesPage />)
    await waitFor(() => expect(api.auth.myPlan).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('New diary')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Diaries:/ })).not.toBeInTheDocument()
  })

  it('shows the owner on a shared card, the counter on an owned one, and the read-only line', async () => {
    ;(api.diaries.list as jest.Mock).mockResolvedValue([
      { ...base, id: 'a', name: 'Mine', role: 'owner', owner_name: 'Me', people: { used: 2, limit: 3 } },
      { ...base, id: 'b', name: 'Old', role: 'owner', owner_name: 'Me', people: { used: 0, limit: 3 } },
      { ...base, id: 'c', name: 'Moms', role: 'viewer', owner_name: 'Mom', people: null },
    ])
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({ ...plusPlan, read_only_diary_ids: ['b'] })
    render(<DiariesPage />)
    expect(await screen.findByText('Shared by Mom')).toBeInTheDocument()
    expect(screen.getByText('Shared with 2 of 3 people')).toBeInTheDocument()
    expect(await screen.findByText('Read-only on the Plus plan')).toBeInTheDocument()
    expect(screen.getAllByText(/Scan every/)).toHaveLength(2)
  })

  it('a person with only shared diaries sees no badge, no New diary, and a way to create one', async () => {
    ;(api.diaries.list as jest.Mock).mockResolvedValue([
      { ...base, id: 'c', name: 'Moms', role: 'viewer', owner_name: 'Mom', people: null },
    ])
    ;(api.auth.myPlan as jest.Mock).mockResolvedValue({ ...plusPlan, tier: 'free', plan_name: 'Free', diaries_limit: 1, diaries_used: 1 })
    render(<DiariesPage />)
    expect(await screen.findByText('Shared by Mom')).toBeInTheDocument()
    expect(screen.queryByText(/New diary/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Photos' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Deleted diaries' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Diaries:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Diaries: |Free plan: 1 diary/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Create your first one/)).not.toBeInTheDocument()
    expect(screen.getByText(/Want a diary of your own\?/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create one' })).toHaveAttribute('href', '/diaries/new')
  })
})
