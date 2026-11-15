import { render, screen, fireEvent } from '@testing-library/react'
import EntryRestorePage from '../page'
import { api, ApiError } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: { diaries: { get: jest.fn() }, entries: { listTrash: jest.fn(), restore: jest.fn() } },
  }
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

it('a diary_read_only restore error shows the read-only banner, not the entry-count sentence', async () => {
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'd1', name: 'D', plan: { plan_name: 'Free' } })
  ;(api.entries.listTrash as jest.Mock).mockResolvedValue([
    { id: 'e1', title: 'Old', entry_date: '2026-05-16', deleted_at: new Date().toISOString() },
  ])
  ;(api.entries.restore as jest.Mock).mockRejectedValue(
    new ApiError(403, 'read only', 'tier_limit', { source: 'diary_read_only', limit: 1, current: 2 }),
  )
  render(<EntryRestorePage />)
  fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
  expect(
    await screen.findByText(/This diary is read-only on the Free plan\. Upgrade, or delete another diary, to use it again\./),
  ).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Upgrade now' })).toHaveAttribute('href', '/plans')
  expect(screen.queryByText(/free-tier limit/)).not.toBeInTheDocument()
})

it('any other restore error shows its message, never a limit sentence or the dead upgrade link', async () => {
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'd1', name: 'D', plan: { plan_name: 'Free' } })
  ;(api.entries.listTrash as jest.Mock).mockResolvedValue([
    { id: 'e1', title: 'Old', entry_date: '2026-05-16', deleted_at: new Date().toISOString() },
  ])
  ;(api.entries.restore as jest.Mock).mockRejectedValue(
    new ApiError(403, 'Not allowed', 'tier_limit', { source: 'diary', limit: 1, current: 1 }),
  )
  render(<EntryRestorePage />)
  fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
  expect(await screen.findByText('Not allowed')).toBeInTheDocument()
  expect(screen.queryByText(/free-tier limit/)).not.toBeInTheDocument()
  expect(document.querySelector('a[href="/account/upgrade"]')).toBeNull()
})
