import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import RulesListPage from '../page'
import { api, ApiError, type Rule } from '@/lib/api'
import { BACKFILL_LIMIT_NOTE, readOnlyBanner } from '@/lib/plan'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { diaries: { get: jest.fn() }, rules: { list: jest.fn(), patch: jest.fn(), delete: jest.fn(), apply: jest.fn() } } }
})
jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})
jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() }
  const params = { diaryId: 'd1' }
  return { useRouter: () => router, useParams: () => params }
})

const rule = { id: 'r1', diary_id: 'd1', name: 'Soccer', enabled: true, last_applied_at: null } as Rule
const freePlan = {
  tier: 'free', plan_name: 'Free', ai_drafts_limit: 5, ai_drafts_left: 3, resets_on: '2026-12-01',
  photos_per_entry: 1, read_only: false, backfill: false, scan_past_days_max: 90,
}
const refused = (source: string) => new ApiError(403, 'tier_limit', 'tier_limit', { source })

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.rules.list as jest.Mock).mockResolvedValue([rule])
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'd1', plan: freePlan })
})

it('a refused toggle shows the read-only copy, never the raw code', async () => {
  ;(api.rules.patch as jest.Mock).mockRejectedValue(refused('diary_read_only'))
  render(<RulesListPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Disable' }))
  expect(await screen.findByText(readOnlyBanner(null))).toBeInTheDocument()
  expect(screen.queryByText(/tier_limit/)).toBeNull()
})

it('a refused Apply (backfill) shows the backfill note, never the raw code', async () => {
  ;(api.rules.apply as jest.Mock).mockRejectedValue(refused('backfill'))
  render(<RulesListPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))
  expect(await screen.findByText(BACKFILL_LIMIT_NOTE)).toBeInTheDocument()
  expect(screen.queryByText(/tier_limit/)).toBeNull()
})

it('Free: Apply days has max 90, a typed 120 is sent as 90, and the note shows', async () => {
  ;(api.rules.apply as jest.Mock).mockResolvedValue({ queued: true })
  render(<RulesListPage />)
  const days = await screen.findByLabelText('Apply days')
  await waitFor(() => expect(days).toHaveAttribute('max', '90'))
  expect(screen.getByText(BACKFILL_LIMIT_NOTE)).toBeInTheDocument()
  fireEvent.change(days, { target: { value: '120' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(api.rules.apply).toHaveBeenCalledWith('r1', 90))
})

it('Plus: Apply days max is 3650 and there is no note', async () => {
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'd1', plan: { ...freePlan, backfill: true, scan_past_days_max: null } })
  render(<RulesListPage />)
  const days = await screen.findByLabelText('Apply days')
  await waitFor(() => expect(screen.queryByText(BACKFILL_LIMIT_NOTE)).toBeNull())
  expect(days).toHaveAttribute('max', '3650')
})
