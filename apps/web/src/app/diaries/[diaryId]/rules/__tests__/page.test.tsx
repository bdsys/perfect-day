import { render, screen, fireEvent } from '@testing-library/react'
import RulesListPage from '../page'
import { api, ApiError, type Rule } from '@/lib/api'
import { BACKFILL_LIMIT_NOTE, readOnlyBanner } from '@/lib/plan'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { rules: { list: jest.fn(), patch: jest.fn(), delete: jest.fn(), apply: jest.fn() } } }
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
const refused = (source: string) => new ApiError(403, 'tier_limit', 'tier_limit', { source })

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.rules.list as jest.Mock).mockResolvedValue([rule])
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
