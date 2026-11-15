import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import DiaryDetailPage from '../page'
import { api, type BackfillRun, type ScanRun } from '@/lib/api'
import { navigateTo } from '@/lib/navigate'

jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn() }))

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
        triggerScan: jest.fn(),
      },
      entries: { list: jest.fn() },
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn() },
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

function finishedScan(events: number, entries: number): ScanRun {
  return {
    id: 'scan-1',
    diary_id: 'diary-1',
    triggered_by: 'manual',
    started_at: '2026-11-14T10:00:00Z',
    completed_at: '2026-11-14T10:00:30Z',
    status: 'success',
    events_calendar: events,
    entries_created: entries,
    errors: null,
  }
}

async function runScan(run: ScanRun) {
  ;(api.diaries.triggerScan as jest.Mock).mockResolvedValue({ queued: true })
  ;(api.diaries.listScanRuns as jest.Mock).mockResolvedValue([run])
  render(<DiaryDetailPage />)
  const scan = await screen.findByRole('button', { name: 'Scan now' })
  await waitFor(() => expect(scan).not.toBeDisabled())
  fireEvent.click(scan)
  await screen.findByText('Scan complete')
}

describe('DiaryDetailPage scan status', () => {
  // A scan's drafts come from events it just fetched, evaluated after the run closes,
  // so "0 new entries" would be wrong while they are being made.
  it('says how many events were checked when the scan made no entries itself', async () => {
    await runScan(finishedScan(37, 0))

    expect(screen.getByText('37 events checked')).toBeInTheDocument()
    expect(screen.queryByText(/0 new entries/)).not.toBeInTheDocument()
  })

  it('counts new entries when the scan made some', async () => {
    await runScan(finishedScan(5, 2))

    expect(screen.getByText('5 events · 2 new entries')).toBeInTheDocument()
  })

  it('uses the singular for one event and one entry', async () => {
    await runScan(finishedScan(1, 1))

    expect(screen.getByText('1 event · 1 new entry')).toBeInTheDocument()
  })

  it('uses the singular for one event checked', async () => {
    await runScan(finishedScan(1, 0))

    expect(screen.getByText('1 event checked')).toBeInTheDocument()
  })
})

describe('DiaryDetailPage connect calendar', () => {
  it('a double tap makes one authorize call and disables the button', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
      can_scan: true,
      calendar: 'none',
      scan_enabled: true,
      scan_interval_minutes: 60,
      last_scan_status: null,
      next_scan_after: null,
      consecutive_failures: 0,
    })
    ;(api.integrations.getGoogleAuthUrl as jest.Mock).mockResolvedValue({
      url: 'https://accounts.google.com/x',
    })
    render(<DiaryDetailPage />)
    const connect = await screen.findByRole('button', { name: 'Connect Google Calendar' })

    fireEvent.click(connect)
    fireEvent.click(connect)

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('https://accounts.google.com/x'))
    expect(api.integrations.getGoogleAuthUrl).toHaveBeenCalledTimes(1)
    expect(connect).toBeDisabled()
  })

  it('lets the person try again after a failure', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
      can_scan: true,
      calendar: 'none',
      scan_enabled: true,
      scan_interval_minutes: 60,
      last_scan_status: null,
      next_scan_after: null,
      consecutive_failures: 0,
    })
    ;(api.integrations.getGoogleAuthUrl as jest.Mock).mockRejectedValueOnce(new Error('nope'))
    render(<DiaryDetailPage />)
    const connect = await screen.findByRole('button', { name: 'Connect Google Calendar' })

    fireEvent.click(connect)
    await waitFor(() => expect(connect).not.toBeDisabled())
    fireEvent.click(connect)

    await waitFor(() => expect(api.integrations.getGoogleAuthUrl).toHaveBeenCalledTimes(2))
  })

  it('Connect works again after Back from Google restores the page from bfcache', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
      can_scan: true,
      calendar: 'none',
      scan_enabled: true,
      scan_interval_minutes: 60,
      last_scan_status: null,
      next_scan_after: null,
      consecutive_failures: 0,
    })
    ;(api.integrations.getGoogleAuthUrl as jest.Mock).mockResolvedValue({
      url: 'https://accounts.google.com/x',
    })
    render(<DiaryDetailPage />)
    const connect = await screen.findByRole('button', { name: 'Connect Google Calendar' })
    fireEvent.click(connect)
    await waitFor(() => expect(navigateTo).toHaveBeenCalled())
    expect(connect).toBeDisabled()

    act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false })) })
    expect(connect).toBeDisabled()
    act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })) })
    expect(connect).not.toBeDisabled()
    fireEvent.click(connect)
    await waitFor(() => expect(api.integrations.getGoogleAuthUrl).toHaveBeenCalledTimes(2))
  })

})

describe('diary page: read-only plan', () => {
  it('shows the banner, disables the write controls and leaves Delete diary enabled', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue({
      id: 'diary-1', name: 'Emma diary',
      plan: { tier: 'free', plan_name: 'Free', ai_drafts_limit: 5, ai_drafts_left: 2, resets_on: '2026-12-01', photos_per_entry: 1, read_only: true },
    })
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    render(<DiaryDetailPage />)
    expect(await screen.findByText('This diary is read-only on the Free plan. Upgrade, or delete another diary, to use it again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scan now' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Backfill' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'New entry' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete diary' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'AI drafts: 2 of 5 left · resets Dec 1' })).toBeInTheDocument()
  })

  it('a Plus diary that is not read-only has no badge and enabled controls', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue({
      id: 'diary-1', name: 'Emma diary',
      plan: { tier: 'plus', plan_name: 'Plus', ai_drafts_limit: null, ai_drafts_left: null, resets_on: '2026-12-01', photos_per_entry: null, read_only: false },
    })
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    render(<DiaryDetailPage />)
    expect(await screen.findByRole('link', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.queryByText(/AI drafts:/)).not.toBeInTheDocument()
  })
})
