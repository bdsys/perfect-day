import { render, screen, fireEvent } from '@testing-library/react'
import DiaryRestorePage from '../page'
import { api, ApiError, type MyPlan } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: { listTrash: jest.fn(), restore: jest.fn() },
      auth: { myPlan: jest.fn() },
    },
  }
})
jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})
jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() }
  return { useRouter: () => router }
})

const plan = (tier: string, planName: string, limit: number) => ({
  tier, plan_name: planName, diaries_limit: limit, diaries_used: limit, ai_drafts_limit: null,
  ai_drafts_used: 0, ai_drafts_left: null, resets_on: '2026-12-01', photos_per_entry: null,
  read_only_diary_ids: [], backfill: false, scan_past_days_max: 90,
}) as MyPlan

beforeEach(() => {
  ;(api.diaries.listTrash as jest.Mock).mockResolvedValue([
    { id: 'd9', name: 'Old diary', deleted_at: new Date().toISOString(), hard_delete_after: null },
  ])
})

it.each([
  ['free', 'Free', 1, 'Free plan: 1 diary.'],
  ['plus', 'Plus', 2, 'Plus plan: 2 diaries.'],
])('a %s diary-limit restore error shows the plan note and Upgrade now', async (tier, name, limit, note) => {
  ;(api.auth.myPlan as jest.Mock).mockResolvedValue(plan(tier, name, limit))
  ;(api.diaries.restore as jest.Mock).mockRejectedValue(
    new ApiError(403, 'limit', 'tier_limit', { source: 'diary', limit, current: limit, required_tier: 'family' }),
  )
  render(<DiaryRestorePage />)
  fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
  expect(await screen.findByText(note)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Upgrade now' })).toHaveAttribute('href', '/plans')
  expect(screen.queryByText(/free-tier/)).not.toBeInTheDocument()
  expect(document.querySelector('a[href="/account/upgrade"]')).toBeNull()
})

it('a diary-limit restore error with a failed plan lookup says "Your plan", not Free', async () => {
  ;(api.auth.myPlan as jest.Mock).mockRejectedValue(new Error('down'))
  ;(api.diaries.restore as jest.Mock).mockRejectedValue(
    new ApiError(403, 'limit', 'tier_limit', { source: 'diary', limit: 1, current: 1 }),
  )
  render(<DiaryRestorePage />)
  fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
  expect(await screen.findByText('Your plan: 1 diary.')).toBeInTheDocument()
})
