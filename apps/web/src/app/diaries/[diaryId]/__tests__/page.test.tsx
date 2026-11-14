import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DiaryDetailPage from '../page'
import { api, type BackfillRun } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: {
        get: jest.fn(),
        getScanConfig: jest.fn(),
        triggerBackfill: jest.fn(),
        getBackfillRun: jest.fn(),
        cancelBackfillRun: jest.fn(),
        listScanRuns: jest.fn(),
      },
      entries: { list: jest.fn() },
      integrations: { list: jest.fn() },
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

const NOTE = "2 events couldn't be saved. Run the backfill again to retry them."

function completedRun(error: string | null): BackfillRun {
  return {
    id: 'run-1',
    diary_id: 'diary-1',
    from_date: '2026-05-01',
    to_date: '2026-05-14',
    sources: ['google_calendar'],
    status: 'completed',
    started_at: '2026-11-14T10:00:00Z',
    completed_at: '2026-11-14T10:01:00Z',
    events_ingested: 5,
    entries_created: 2,
    error,
  }
}

async function runBackfill(run: BackfillRun) {
  ;(api.diaries.triggerBackfill as jest.Mock).mockResolvedValue(run)
  ;(api.diaries.getBackfillRun as jest.Mock).mockResolvedValue(run)
  render(<DiaryDetailPage />)
  const open = await screen.findByRole('button', { name: 'Backfill' })
  await waitFor(() => expect(open).not.toBeDisabled())
  fireEvent.click(open)
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-05-01' } })
  fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-05-14' } })
  fireEvent.click(screen.getByRole('button', { name: 'Start backfill' }))
  await screen.findByText('Backfill complete')
}

beforeEach(() => {
  jest.clearAllMocks()
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'diary-1', name: 'Emma diary' })
  ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
    can_scan: true,
    calendar: 'connected',
    scan_enabled: true,
    scan_interval_minutes: 60,
    last_scan_status: null,
    next_scan_after: null,
    consecutive_failures: 0,
  })
  ;(api.diaries.listScanRuns as jest.Mock).mockResolvedValue([])
  ;(api.entries.list as jest.Mock).mockResolvedValue([])
  ;(api.integrations.list as jest.Mock).mockResolvedValue([])
})

describe('DiaryDetailPage backfill status', () => {
  it('shows the note on a completed backfill that skipped events', async () => {
    await runBackfill(completedRun(NOTE))

    expect(screen.getByText(NOTE)).toBeInTheDocument()
    expect(screen.getByText('5 events · 2 new entries')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(NOTE)
  })

  it('shows nothing extra on a completed backfill with no note', async () => {
    await runBackfill(completedRun(null))

    expect(screen.getByText('5 events · 2 new entries')).toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(screen.queryByText(/couldn't be saved/)).not.toBeInTheDocument()
  })
})
