import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import DiaryDetailPage from '../page'
import { api, ApiError, type BackfillRun, type ScanRun } from '@/lib/api'
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
      entries: { list: jest.fn(), get: jest.fn(), aiStatus: jest.fn() },
      sharing: { remove: jest.fn(), decline: jest.fn() },
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn() },
      wishLists: { comingUp: jest.fn() },
    },
  }
})

// One stable value, like the real provider: a new object per render would re-run the page's load effect.
jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})

const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
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
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me' })
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
  ;(api.wishLists.comingUp as jest.Mock).mockResolvedValue({ wish_lists: [] })
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
      id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me',
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
      id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me',
      plan: { tier: 'plus', plan_name: 'Plus', ai_drafts_limit: null, ai_drafts_left: null, resets_on: '2026-12-01', photos_per_entry: null, read_only: false },
    })
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    render(<DiaryDetailPage />)
    expect(await screen.findByRole('link', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.queryByText(/AI drafts:/)).not.toBeInTheDocument()
  })
})

describe('diary page: plan copy', () => {
  const freeDiary = {
    id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me',
    plan: { tier: 'free', plan_name: 'Free', ai_drafts_limit: 5, ai_drafts_left: 2, resets_on: '2026-12-01', photos_per_entry: 1, read_only: false, backfill: false, scan_past_days_max: 90 },
  }

  it('the AI detail text uses " · resets", like the badge', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(freeDiary)
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    render(<DiaryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: /AI drafts: 2 of 5 left/ }))
    expect(screen.getByText('2 of 5 left · resets Dec 1')).toBeInTheDocument()
    expect(screen.queryByText(/, resets/)).toBeNull()
  })

  it('a backfill refused with source backfill shows the plan copy, not the raw code', async () => {
    // A downgrade after the page loaded: the page still thinks Backfill is on.
    ;(api.diaries.get as jest.Mock).mockResolvedValue({ ...freeDiary, plan: { ...freeDiary.plan, backfill: true } })
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    ;(api.diaries.triggerBackfill as jest.Mock).mockRejectedValue(
      new ApiError(403, 'tier_limit', 'tier_limit', { source: 'backfill' }),
    )
    render(<DiaryDetailPage />)
    const open = await screen.findByRole('button', { name: 'Backfill' })
    await waitFor(() => expect(open).not.toBeDisabled())
    fireEvent.click(open)
    fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-05-01' } })
    fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-05-14' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start backfill' }))
    expect(await screen.findByText('Backfill is on Plus and Family. Free scans look back 90 days.')).toBeInTheDocument()
    expect(screen.queryByText(/tier_limit/)).toBeNull()
  })

  it('a scan refused with source backfill shows the plan copy', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(freeDiary)
    ;(api.entries.list as jest.Mock).mockResolvedValue([])
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    ;(api.diaries.triggerScan as jest.Mock).mockRejectedValue(
      new ApiError(403, 'tier_limit', 'tier_limit', { source: 'backfill' }),
    )
    render(<DiaryDetailPage />)
    const scan = await screen.findByRole('button', { name: 'Scan now' })
    await waitFor(() => expect(scan).not.toBeDisabled())
    fireEvent.click(scan)
    expect(await screen.findByText('Backfill is on Plus and Family. Free scans look back 90 days.')).toBeInTheDocument()
  })
})

describe('diary page: gray out what the API refuses', () => {
  const planOf = (over: Record<string, unknown>) => ({
    id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me',
    plan: { tier: 'free', plan_name: 'Free', ai_drafts_limit: 5, ai_drafts_left: 2, resets_on: '2026-12-01', photos_per_entry: 1, read_only: false, backfill: false, scan_past_days_max: 90, ...over },
  })
  const BACKFILL_NOTE = 'Backfill is on Plus and Family. Free scans look back 90 days.'

  it('read-only disables Connect Google Calendar', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(planOf({ read_only: true }))
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
      can_scan: true, calendar: 'none', scan_enabled: true, scan_interval_minutes: 60,
      last_scan_status: null, next_scan_after: null, consecutive_failures: 0,
    })
    render(<DiaryDetailPage />)
    await screen.findByText(/read-only on the Free plan/)
    expect(screen.getByRole('button', { name: 'Connect Google Calendar' })).toBeDisabled()
  })

  it('Free: Backfill is grayed with the note and an Upgrade now link to /plans', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(planOf({}))
    render(<DiaryDetailPage />)
    const btn = await screen.findByRole('button', { name: 'Backfill' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Scan now' })).toBeEnabled())
    expect(btn).toBeDisabled()
    expect(document.getElementById(btn.getAttribute('aria-describedby')!)).toHaveTextContent(BACKFILL_NOTE)
    const upgrade = within(btn.parentElement!).getByRole('link', { name: 'Upgrade now' })
    expect(upgrade).toHaveAttribute('href', '/plans')
  })

  it('Free: a co-parent with edit access sees no Upgrade now under Backfill', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(planOf({}))
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({
      can_scan: false, calendar: null, scan_enabled: true, scan_interval_minutes: 60,
      last_scan_status: null, next_scan_after: null, consecutive_failures: 0,
    })
    render(<DiaryDetailPage />)
    const btn = await screen.findByRole('button', { name: 'Backfill' })
    await waitFor(() => expect(api.diaries.getScanConfig).toHaveBeenCalled())
    expect(btn).toBeDisabled()
    expect(within(btn.parentElement!).queryByRole('link', { name: 'Upgrade now' })).toBeNull()
    expect(screen.queryByText(BACKFILL_NOTE)).toBeNull()
  })

  it('Free: Past days has max 90, a typed 365 is sent as 90, and the note shows', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(planOf({}))
    ;(api.diaries.triggerScan as jest.Mock).mockResolvedValue({ queued: true })
    render(<DiaryDetailPage />)
    const gear = await screen.findByRole('button', { name: 'Scan options' })
    await waitFor(() => expect(gear).toBeEnabled())
    fireEvent.click(gear)
    const past = screen.getByLabelText('Past days')
    expect(past).toHaveAttribute('max', '90')
    expect(screen.getAllByText(BACKFILL_NOTE).length).toBeGreaterThan(0)
    fireEvent.change(past, { target: { value: '365' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run scan' }))
    await waitFor(() => expect(api.diaries.triggerScan).toHaveBeenCalledWith('diary-1', { past_days: 90, future_days: 90 }))
  })

  it('Plus: Backfill is enabled and Past days max is 3650 with no note', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(planOf({ tier: 'plus', plan_name: 'Plus', backfill: true, scan_past_days_max: null }))
    render(<DiaryDetailPage />)
    const btn = await screen.findByRole('button', { name: 'Backfill' })
    await waitFor(() => expect(btn).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Scan options' }))
    expect(screen.getByLabelText('Past days')).toHaveAttribute('max', '3650')
    expect(screen.queryByText(BACKFILL_NOTE)).toBeNull()
  })
})

describe('diary page: New entry from Google Calendar needs a connected calendar', () => {
  const scanConfig = (calendar: string) => ({
    can_scan: true, calendar, scan_enabled: true, scan_interval_minutes: 60,
    last_scan_status: null, next_scan_after: null, consecutive_failures: 0,
  })
  const HINT = 'Connect Google Calendar to scan for new events, backfill past ones and make entries from your events.'

  it('is grayed with the hint when Calendar was never connected', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue(scanConfig('none'))
    render(<DiaryDetailPage />)
    const button = await screen.findByRole('button', { name: 'New entry from Google Calendar' })
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveAttribute('aria-describedby', 'calendar-hint')
    expect(screen.getByText(HINT)).toBeInTheDocument()
  })

  it('is grayed when the connection is lost', async () => {
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue(scanConfig('lost'))
    render(<DiaryDetailPage />)
    const button = await screen.findByRole('button', { name: 'New entry from Google Calendar' })
    await waitFor(() => expect(button).toBeDisabled())
  })

  it('is enabled when Calendar is connected', async () => {
    render(<DiaryDetailPage />)
    const button = await screen.findByRole('button', { name: 'New entry from Google Calendar' })
    await waitFor(() => expect(api.diaries.getScanConfig).toHaveBeenCalled())
    expect(button).not.toBeDisabled()
  })
})

describe('diary page: AI status', () => {
  function entry(id: string, over: Record<string, unknown> = {}) {
    return {
      id, diary_id: 'diary-1', entry_date: '2026-11-10', entry_end_date: null, title: null,
      body_markdown: null, status: 'draft', events: [], ai_status: null, ...over,
    }
  }

  beforeEach(() => jest.useFakeTimers())
  afterEach(() => jest.useRealTimers())

  async function tick(ms = 3000) {
    await act(async () => { await jest.advanceTimersByTimeAsync(ms) })
  }

  it('shows the badge on pending entries', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([entry('e1', { ai_status: 'queued' })])
    ;(api.entries.aiStatus as jest.Mock).mockResolvedValue({ e1: 'queued' })
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Queued')).toBeInTheDocument()
  })

  it('polls while pending and refetches a finished entry', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([entry('e1', { ai_status: 'queued' })])
    ;(api.entries.aiStatus as jest.Mock)
      .mockResolvedValueOnce({ e1: 'writing' })
      .mockResolvedValue({})
    ;(api.entries.get as jest.Mock).mockResolvedValue(entry('e1', { title: 'Beach day' }))
    render(<DiaryDetailPage />)
    expect(await screen.findByText('AI is writing…')).toBeInTheDocument()
    await tick()
    await tick()
    expect(await screen.findByText('Beach day')).toBeInTheDocument()
    expect(screen.queryByTestId('ai-status')).toBeNull()
    expect(api.entries.get).toHaveBeenCalledTimes(1)
    expect(api.entries.get).toHaveBeenCalledWith('e1')
  })

  it('does not poll when nothing is pending', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([entry('e1')])
    render(<DiaryDetailPage />)
    await screen.findByText('(no title yet)')
    await tick()
    await tick()
    expect(api.entries.aiStatus).not.toHaveBeenCalled()
  })

  it('stops polling once nothing is pending any more', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([entry('e1', { ai_status: 'queued' })])
    ;(api.entries.aiStatus as jest.Mock).mockResolvedValue({})
    ;(api.entries.get as jest.Mock).mockResolvedValue(entry('e1', { title: 'Done' }))
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Done')).toBeInTheDocument()
    await tick()
    await tick()
    expect(api.entries.aiStatus).toHaveBeenCalledTimes(1)
  })

  it('drops an entry that was deleted elsewhere', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([
      entry('e1', { title: 'Gone', ai_status: 'queued' }),
      entry('e2', { title: 'Old', ai_status: 'queued' }),
    ])
    ;(api.entries.aiStatus as jest.Mock).mockResolvedValue({})
    ;(api.entries.get as jest.Mock).mockImplementation(async (id: string) => {
      if (id === 'e1') throw new ApiError(404, 'Not found')
      return entry('e2', { title: 'Fresh' })
    })
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Fresh')).toBeInTheDocument()
    expect(screen.queryByText('Gone')).toBeNull()
    expect(screen.queryByTestId('ai-status')).toBeNull()
    await tick()
    await tick()
    expect(api.entries.aiStatus).toHaveBeenCalledTimes(1)
  })

  it('one failed re-fetch does not freeze the others', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([
      entry('e1', { title: 'One', ai_status: 'queued' }),
      entry('e2', { title: 'Two', ai_status: 'queued' }),
    ])
    ;(api.entries.aiStatus as jest.Mock).mockResolvedValue({})
    ;(api.entries.get as jest.Mock).mockImplementation(async (id: string) => {
      if (id === 'e1') throw new ApiError(500, 'boom')
      return entry('e2', { title: 'Two done' })
    })
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Two done')).toBeInTheDocument()
    expect(screen.getAllByTestId('ai-status')).toHaveLength(1)
    expect(screen.getByText('One')).toBeInTheDocument()
    const before = (api.entries.get as jest.Mock).mock.calls.filter(([id]) => id === 'e1').length
    await tick()
    const after = (api.entries.get as jest.Mock).mock.calls.filter(([id]) => id === 'e1').length
    expect(after).toBeGreaterThan(before)
  })

  it('stops polling after repeated failures', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([entry('e1', { ai_status: 'queued' })])
    ;(api.entries.aiStatus as jest.Mock).mockRejectedValue(new Error('down'))
    render(<DiaryDetailPage />)
    await screen.findByText('Queued')
    for (let i = 0; i < 5 + 2; i++) await tick()
    expect(api.entries.aiStatus).toHaveBeenCalledTimes(5)
  })

  it('after polling gives up, pending badges stop pretending', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([
      entry('e1', { ai_status: 'queued' }),
      entry('e2', { ai_status: 'writing' }),
    ])
    ;(api.entries.aiStatus as jest.Mock).mockRejectedValue(new Error('down'))
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Queued')).toBeInTheDocument()
    for (let i = 0; i < 5 + 2; i++) await tick()
    const badges = screen.getAllByTestId('ai-status')
    expect(badges).toHaveLength(2)
    badges.forEach((b) => {
      expect(b).toHaveTextContent(/^Refresh to check$/)
      expect(b.querySelector('.spinner')).toBeNull()
    })
  })

  it('updates an entry loaded by load more', async () => {
    const first = Array.from({ length: 20 }, (_, i) => entry(`a${i}`, { title: `T${i}` }))
    ;(api.entries.list as jest.Mock).mockImplementation(async (_d: string, params: { cursor?: string }) =>
      params.cursor ? [entry('old', { ai_status: 'writing' })] : first,
    )
    ;(api.entries.aiStatus as jest.Mock).mockResolvedValueOnce({ old: 'queued' }).mockResolvedValue({})
    ;(api.entries.get as jest.Mock).mockResolvedValue(entry('old', { title: 'Old one' }))
    render(<DiaryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: /load|older|more/i }))
    expect(await screen.findByText('Queued')).toBeInTheDocument()
    await tick()
    expect(await screen.findByText('Old one')).toBeInTheDocument()
    expect(api.entries.get).toHaveBeenCalledWith('old')
  })
})

describe('DiaryDetailPage roles', () => {
  const memberDiary = (role: string, extra: object = {}) => ({
    id: 'diary-1', name: 'Emma diary', role, owner_name: 'Sam', ...extra,
  })

  it('shows an owner the Share link and no Leave', async () => {
    render(<DiaryDetailPage />)
    expect(await screen.findByRole('link', { name: 'Share' })).toHaveAttribute('href', '/diaries/diary-1/sharing')
    expect(screen.queryByRole('button', { name: 'Leave diary' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Shared by/)).not.toBeInTheDocument()
  })

  it('shows a viewer only the title, the byline and the entries', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('viewer'))
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({ can_scan: false, calendar: 'connected' })
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Shared by Sam')).toBeInTheDocument()
    expect(screen.getByText('No entries shared yet.')).toBeInTheDocument()
    for (const name of ['Scan now', 'Backfill', 'New entry', 'Delete diary', 'All', 'Draft', 'Published', 'Auto-Creation Rules']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    for (const name of ['Settings', 'Photos', 'Deleted entries', 'Share']) {
      expect(screen.queryByRole('link', { name })).not.toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'Leave diary' })).toBeInTheDocument()
  })

  it('a viewer sees no published badge on entry cards, an editor does', async () => {
    ;(api.entries.list as jest.Mock).mockResolvedValue([
      { id: 'e1', status: 'published', entry_date: '2026-11-01', entry_end_date: null, title: 'First day', body_markdown: 'b', events: [] },
    ])
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('viewer'))
    const { unmount } = render(<DiaryDetailPage />)
    expect(await screen.findByText('First day')).toBeInTheDocument()
    expect(screen.queryByText('published')).not.toBeInTheDocument()
    unmount()
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('editor'))
    render(<DiaryDetailPage />)
    expect(await screen.findByText('First day')).toBeInTheDocument()
    expect(screen.getByText('published')).toBeInTheDocument()
  })

  it('a viewer still gets Load older entries', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('viewer'))
    ;(api.entries.list as jest.Mock).mockResolvedValue(
      Array.from({ length: 20 }, (_, i) => ({
        id: `e${i}`, status: 'published', entry_date: '2026-11-01', entry_end_date: null,
        title: `T${i}`, body_markdown: 'b', events: [],
      })),
    )
    render(<DiaryDetailPage />)
    expect(await screen.findByRole('button', { name: 'Load older entries' })).toBeInTheDocument()
  })

  it('an editor loses Settings and Delete diary but keeps the rest', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('editor'))
    ;(api.diaries.getScanConfig as jest.Mock).mockResolvedValue({ can_scan: false, calendar: 'connected' })
    render(<DiaryDetailPage />)
    expect(await screen.findByText('Shared by Sam')).toBeInTheDocument()
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete diary' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New entry' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Deleted entries' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Leave diary' })).toBeInTheDocument()
  })

  it('Leave diary confirms, removes me and goes to the diaries list', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(memberDiary('viewer'))
    ;(api.sharing.remove as jest.Mock).mockResolvedValue(undefined)
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true)
    render(<DiaryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Leave diary' }))
    expect(confirm).toHaveBeenCalledWith("Leave Emma diary? You'll need a new invite to see it again.")
    await waitFor(() => expect(api.sharing.remove).toHaveBeenCalledWith('diary-1', 'u1'))
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/diaries'))
    confirm.mockRestore()
  })

  it('shows the deletion notice to a member', async () => {
    ;(api.diaries.get as jest.Mock).mockResolvedValue(
      memberDiary('viewer', { deletion_notice: { owner_name: 'Sam', deletes_on: '2026-11-29T12:00:00Z', becomes_yours: false } }),
    )
    render(<DiaryDetailPage />)
    expect(await screen.findByText(/Sam is deleting their account\. Emma diary will be deleted on/)).toBeInTheDocument()
  })
})

describe('DiaryDetailPage wish lists', () => {
  it('links to the wish lists and shows Coming up only when it has rows', async () => {
    ;(api.wishLists.comingUp as jest.Mock).mockResolvedValue({
      wish_lists: [{ id: 'wl1', diary_id: 'diary-1', title: "Emma's 6th birthday", list_date: '2026-03-14', status: 'published', item_count: 2 }],
    })
    render(<DiaryDetailPage />)
    const row = await screen.findByRole('link', { name: "Wish list · Emma's 6th birthday · Sat 14 Mar" })
    expect(row).toHaveAttribute('href', '/diaries/diary-1/wish-lists/wl1')
    expect(screen.getByRole('link', { name: 'Wish lists' })).toHaveAttribute('href', '/diaries/diary-1/wish-lists')
  })

  it('hides Coming up when it is empty', async () => {
    render(<DiaryDetailPage />)
    await screen.findByRole('link', { name: 'Wish lists' })
    expect(screen.queryByText('Coming up')).not.toBeInTheDocument()
  })
})
