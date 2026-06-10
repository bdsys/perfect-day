import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DiarySettingsPage from '../page'
import { api } from '@/lib/api'

const mockDiary = {
  id: 'diary-1',
  name: 'Emma diary',
  slug: 'emma-diary',
  timezone: 'America/Los_Angeles',
  subject_name: 'Emma',
  subject_relation: 'child',
  voice_override: null,
  tone_hint: 'warm, narrative',
  scan_interval_minutes: 60,
  scan_enabled: true,
  lat: null,
  lon: null,
  created_at: '2026-01-01T00:00:00Z',
  notifications_muted: false,
}

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: {
        get: jest.fn(),
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
  useParams: () => ({ diaryId: 'diary-1' }),
}))

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.diaries.get as jest.Mock).mockResolvedValue(mockDiary)
  ;(api.diaries.patch as jest.Mock).mockResolvedValue({ ...mockDiary, tone_hint: 'playful' })
})

describe('DiarySettingsPage', () => {
  it('loads and pre-fills the form with the diary values', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => {
      expect(screen.getByLabelText('Name')).toBeInTheDocument()
    })
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Emma diary')
    expect((screen.getByLabelText(/their name/i) as HTMLInputElement).value).toBe('Emma')
    expect((screen.getByLabelText(/tone/i) as HTMLInputElement).value).toBe('warm, narrative')
    // My child button should be active
    expect(screen.getByRole('button', { name: /my child/i }).className).toMatch(/btn-primary/)
  })

  it('shows "Save changes" button', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument())
  })

  it('calls api.diaries.patch and redirects on save', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => expect(screen.getByLabelText('Name')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/tone/i), { target: { value: 'playful' } })
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(api.diaries.patch).toHaveBeenCalledTimes(1))
    expect(api.diaries.patch).toHaveBeenCalledWith(
      'diary-1',
      expect.objectContaining({ tone_hint: 'playful' })
    )
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/diaries/diary-1'))
  })

  it('renders a scan-enabled toggle', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /scan enabled/i })).toBeInTheDocument()
    })
  })

  it('patches scan_enabled when toggled', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /scan enabled/i })).toBeInTheDocument())

    const toggle = screen.getByRole('checkbox', { name: /scan enabled/i })
    expect(toggle).toBeChecked()
    fireEvent.click(toggle)

    await waitFor(() => expect(api.diaries.patch).toHaveBeenCalledWith('diary-1', { scan_enabled: false }))
  })

  it('has a back link to the diary detail', async () => {
    render(<DiarySettingsPage />)
    await waitFor(() => expect(screen.getByRole('link', { name: /diary/i })).toBeInTheDocument())
    const back = screen.getByRole('link', { name: /diary/i })
    expect(back).toHaveAttribute('href', '/diaries/diary-1')
  })
})
