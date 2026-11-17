import { render, screen, waitFor } from '@testing-library/react'
import CalendarPickPage from '../page'
import { api } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: { getScanConfig: jest.fn() },
      calendarEvents: { list: jest.fn(), createFromEvent: jest.fn() },
    },
  }
})

jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false }),
}))

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useParams: () => ({ diaryId: 'diary-1' }),
}))

const scanConfig = (calendar: string) => ({
  can_scan: true, calendar, scan_enabled: true, scan_interval_minutes: 60,
  last_scan_status: null, next_scan_after: null, consecutive_failures: 0,
})

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.calendarEvents.list as jest.Mock).mockResolvedValue([])
})

describe('calendar pick page', () => {
  it('says Calendar is not connected, links back to the diary, and loads no events', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue(scanConfig('none'))
    render(<CalendarPickPage />)
    expect(await screen.findByText(/Google Calendar isn.t connected/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Back to the diary/ })).toHaveAttribute('href', '/diaries/diary-1')
    expect(api.calendarEvents.list).not.toHaveBeenCalled()
  })

  it('shows the month and loads events when Calendar is connected', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue(scanConfig('connected'))
    render(<CalendarPickPage />)
    await waitFor(() => expect(api.calendarEvents.list).toHaveBeenCalled())
    expect(screen.queryByText(/Google Calendar isn.t connected/)).not.toBeInTheDocument()
  })
})
