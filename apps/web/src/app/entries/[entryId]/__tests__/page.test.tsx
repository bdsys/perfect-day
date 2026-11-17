import { render, screen, fireEvent, act } from '@testing-library/react'
import EntryDetailPage from '../page'
import { api, ApiError, type Entry, type OwnerPlan, type PhotoImport } from '@/lib/api'

jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('@/components/PhotoThumbnail', () => ({ PhotoThumbnail: () => <span data-testid="thumb" /> }))
jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      entries: { get: jest.fn(), regenerate: jest.fn(), publish: jest.fn(), acceptSuggestion: jest.fn() },
      photoImports: { listForEntry: jest.fn(), create: jest.fn(), cancel: jest.fn(), dismiss: jest.fn() },
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn() },
      photos: { detachFromEntry: jest.fn(), list: jest.fn() },
    },
  }
})
// Stable references: a fresh user/router object each render would re-run the page's load effect.
jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})
jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() }
  const params = { entryId: 'e1' }
  const search = new URLSearchParams()
  return { useRouter: () => router, useParams: () => params, useSearchParams: () => search }
})

const get = api.entries.get as jest.Mock
const listForEntry = api.photoImports.listForEntry as jest.Mock

function entry(photos: { id: string }[]): Entry {
  return {
    id: 'e1', diary_id: 'd1', entry_date: '2026-05-16', entry_end_date: null, title: 'T', body_markdown: 'Body',
    body_source: 'llm', status: 'draft', events: [], photos, enrichments: [], suggestion: null, location: null,
    tz_override: null, can_edit: true, updated_at: '2026-05-16T00:00:00Z',
  } as unknown as Entry
}
const imp = (over: Partial<PhotoImport>): PhotoImport =>
  ({ id: 'i1', entry_id: 'e1', status: 'copying', added_count: 0, skipped_videos: 0, failed_names: [], ...over }) as PhotoImport

afterEach(() => {
  jest.clearAllMocks()
  jest.useRealTimers()
})

describe('entry page: Google import notice in Edit mode', () => {
  it('keeps polling while Edit is open, hides the notice, and refreshes photos after leaving Edit', async () => {
    jest.useFakeTimers()
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    get.mockResolvedValueOnce(entry([])).mockResolvedValue(entry([{ id: 'p1' }]))
    listForEntry
      .mockResolvedValueOnce([imp({ status: 'copying' })])
      .mockResolvedValue([imp({ status: 'done', added_count: 1 })])
    render(<EntryDetailPage />)
    await act(async () => {})
    expect(screen.getByText('Copying photos from Google Photos…')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByText('Copying photos from Google Photos…')).not.toBeVisible()
    expect(screen.queryByTestId('thumb')).not.toBeInTheDocument()

    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(listForEntry).toHaveBeenCalledTimes(2) // polling continued while editing

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await act(async () => {})
    expect(screen.getByTestId('thumb')).toBeInTheDocument()
    expect(screen.getByText('1 photo added from Google Photos')).toBeVisible()
  })

  it('in view mode the notice comes before the Photos heading', async () => {
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    get.mockResolvedValue(entry([{ id: 'p1' }]))
    listForEntry.mockResolvedValue([imp({ status: 'copying' })])
    render(<EntryDetailPage />)
    const notice = await screen.findByText('Copying photos from Google Photos…')
    const heading = screen.getByRole('heading', { name: 'Photos' })
    expect(notice.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('an import finishing mid-edit does not wipe what was typed', async () => {
    jest.useFakeTimers()
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    get.mockResolvedValueOnce(entry([])).mockResolvedValue(entry([{ id: 'p1' }]))
    listForEntry
      .mockResolvedValueOnce([imp({ status: 'copying' })])
      .mockResolvedValue([imp({ status: 'done', added_count: 1 })])
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My typed title' } })
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'My typed body' } })
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(get).toHaveBeenCalledTimes(2) // the refresh ran
    expect(screen.getByLabelText('Title')).toHaveValue('My typed title')
    expect(screen.getByLabelText('Body')).toHaveValue('My typed body')
  })
})

const freePlan = (over: Partial<OwnerPlan> = {}): OwnerPlan => ({
  tier: 'free', plan_name: 'Free', ai_drafts_limit: 5, ai_drafts_left: 3, resets_on: '2026-12-01',
  photos_per_entry: 1, read_only: false, backfill: false, scan_past_days_max: 90, ...over,
})
const withPlan = (plan: OwnerPlan | null, over: Partial<Entry> = {}): Entry =>
  ({ ...entry([]), plan, ...over }) as Entry

describe('entry page: plan limits', () => {
  beforeEach(() => {
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    listForEntry.mockResolvedValue([])
  })

  it('grays out the AI button at the limit and points it at the AI note', async () => {
    get.mockResolvedValue(withPlan(freePlan({ ai_drafts_left: 0 }), { body_markdown: 'Body', events: [{ id: 'ev' }] as never }))
    render(<EntryDetailPage />)
    const btn = await screen.findByRole('button', { name: /regenerate with ai/i })
    expect(btn).toBeDisabled()
    const note = document.getElementById(btn.getAttribute('aria-describedby')!)
    expect(note).toHaveTextContent('No AI drafts left this month. Resets Dec 1.')
    expect(screen.getByRole('button', { name: 'AI drafts: 0 of 5 left · resets Dec 1' })).toBeInTheDocument()
  })

  it('shows the entry note only when the last generation hit the limit and none are left', async () => {
    const failed = { status: 'failed', error: 'ai_draft_limit', created_at: 'x' } as never
    get.mockResolvedValue(withPlan(freePlan({ ai_drafts_left: 0 }), { last_generation: failed }))
    const { unmount } = render(<EntryDetailPage />)
    expect(await screen.findByText('Out of AI drafts until Dec 1. Write it yourself, or generate it then.')).toBeInTheDocument()
    unmount()

    get.mockResolvedValue(withPlan(freePlan({ ai_drafts_left: 5 }), { last_generation: failed, events: [{ id: 'ev' }] as never }))
    render(<EntryDetailPage />)
    const btn = await screen.findByRole('button', { name: /regenerate with ai/i })
    expect(screen.queryByText(/Out of AI drafts until/)).not.toBeInTheDocument()
    expect(btn).toBeEnabled()
  })

  it('shows no badge on a Plus plan', async () => {
    get.mockResolvedValue(withPlan(freePlan({ tier: 'plus', plan_name: 'Plus', ai_drafts_limit: null, ai_drafts_left: null, photos_per_entry: null })))
    render(<EntryDetailPage />)
    await screen.findByRole('button', { name: 'Edit' })
    expect(screen.queryByText(/AI drafts:/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Upgrade now' })).not.toBeInTheDocument()
  })

  it('disables Add photos with the photo note when the entry is full', async () => {
    get.mockResolvedValue(withPlan(freePlan(), { photos: [{ id: 'p1' }] as never }))
    render(<EntryDetailPage />)
    const btn = await screen.findByRole('button', { name: /add photos/i })
    expect(btn).toBeDisabled()
    expect(screen.getByText('Free plan: 1 photo per entry.')).toBeInTheDocument()
  })

  it('read-only disables Edit and Publish, leaves Delete enabled and shows the banner', async () => {
    get.mockResolvedValue(withPlan(freePlan({ read_only: true })))
    render(<EntryDetailPage />)
    expect(await screen.findByText('This diary is read-only on the Free plan. Upgrade, or delete another diary, to use it again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publish' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled()
  })

  it('read-only disables Accept, Edit location and photo removal', async () => {
    const suggestion = { title: 'S', body_markdown: 'New', flagged_tokens: [], body_source: 'llm', created_at: new Date().toISOString() } as never
    get.mockResolvedValue(withPlan(freePlan({ read_only: true }), {
      suggestion, photos: [{ id: 'p1' }] as never, location: { name: 'Here', lat: null, lon: null, source: 'parent' },
    }))
    render(<EntryDetailPage />)
    expect(await screen.findByRole('button', { name: 'Accept' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit location' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove photo from entry' })).toBeDisabled()
  })

  it('a tier_limit from regenerate shows the AI note, never the raw code, and keeps the badge', async () => {
    get.mockResolvedValue(withPlan(freePlan(), { events: [{ id: 'ev' }] as never }))
    ;(api.entries.regenerate as jest.Mock).mockRejectedValue(
      new ApiError(403, 'limit reached', 'tier_limit', { source: 'ai_draft', resets_on: '2026-12-01' }),
    )
    render(<EntryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: /regenerate with ai/i }))
    expect((await screen.findAllByText('No AI drafts left this month. Resets Dec 1.')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /AI drafts: 3 of 5 left/ })).toBeInTheDocument()
  })

  it('an action response with plan null does not blank the badge or re-enable controls', async () => {
    get.mockResolvedValue(withPlan(freePlan({ ai_drafts_left: 0 }), { events: [{ id: 'ev' }] as never }))
    ;(api.entries.publish as jest.Mock).mockResolvedValue(withPlan(null, { status: 'published', events: [{ id: 'ev' }] as never }))
    render(<EntryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Publish' }))
    await screen.findByRole('button', { name: 'Unpublish' })
    expect(screen.getByRole('button', { name: /AI drafts: 0 of 5 left/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /regenerate with ai/i })).toBeDisabled()
  })

  it('a regenerate refused as diary_read_only shows the read-only copy, not the AI note', async () => {
    get.mockResolvedValue(withPlan(freePlan(), { events: [{ id: 'ev' }] as never }))
    ;(api.entries.regenerate as jest.Mock).mockRejectedValue(
      new ApiError(403, 'tier_limit', 'tier_limit', { source: 'diary_read_only' }),
    )
    render(<EntryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: /regenerate with ai/i }))
    expect(await screen.findByText(/This diary is read-only on the Free plan/)).toBeInTheDocument()
    expect(screen.queryByText(/No AI drafts left/)).not.toBeInTheDocument()
    expect(screen.queryByText(/tier_limit/)).not.toBeInTheDocument()
  })

  it('a refused suggestion accept shows mapped copy, never the raw code', async () => {
    const suggestion = { title: 'S', body_markdown: 'New', flagged_tokens: [], body_source: 'llm', created_at: '2026-05-16T00:00:00Z' }
    get.mockResolvedValue(withPlan(freePlan(), { suggestion: suggestion as never }))
    ;(api.entries.acceptSuggestion as jest.Mock).mockRejectedValue(
      new ApiError(403, 'tier_limit', 'tier_limit', { source: 'diary_read_only' }),
    )
    render(<EntryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }))
    expect((await screen.findAllByText(/This diary is read-only on the Free plan/)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/tier_limit/)).toBeNull()
  })

  it('the AI detail text uses " · resets", like the badge', async () => {
    get.mockResolvedValue(withPlan(freePlan()))
    render(<EntryDetailPage />)
    fireEvent.click(await screen.findByRole('button', { name: /AI drafts: 3 of 5 left/ }))
    expect(screen.getByText('3 of 5 left · resets Dec 1')).toBeInTheDocument()
    expect(screen.queryByText(/, resets/)).toBeNull()
  })
})

describe('entry page: event moved or removed note', () => {
  const NOTE = /The calendar event this entry was drafted from was moved or removed/

  beforeEach(() => {
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    listForEntry.mockResolvedValue([])
  })

  it('shows on an untouched rule draft with no events left', async () => {
    get.mockResolvedValue({ ...entry([{ id: 'p1' }]), creation_source: 'rule' })
    render(<EntryDetailPage />)
    expect(await screen.findByText(NOTE)).toBeInTheDocument()
  })

  it('is not shown to someone who cannot edit the entry', async () => {
    get.mockResolvedValue({ ...entry([]), creation_source: 'rule', can_edit: false })
    render(<EntryDetailPage />)
    await screen.findByText('Body')
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('is not shown once the parent has edited the text', async () => {
    get.mockResolvedValue({ ...entry([]), creation_source: 'calendar_pick', body_source: 'user' })
    render(<EntryDetailPage />)
    await screen.findByText('Body')
    expect(screen.queryByText(NOTE)).toBeNull()
  })
})

describe('entry page: AI working notice', () => {
  const WRITING = 'AI is writing this entry. Wait a moment for the draft, or edit anyway.'
  const QUEUED = 'AI will write this entry shortly. Wait for the draft, or edit anyway.'
  const ai = (over: Partial<Entry>): Entry => ({ ...entry([]), ...over }) as Entry

  beforeEach(() => {
    ;(api.integrations.list as jest.Mock).mockResolvedValue([])
    listForEntry.mockResolvedValue([])
  })

  it('shows the writing notice', async () => {
    get.mockResolvedValue(ai({ ai_status: 'writing' }))
    render(<EntryDetailPage />)
    expect(await screen.findByText(WRITING)).toBeInTheDocument()
    expect(screen.queryByText(QUEUED)).not.toBeInTheDocument()
  })

  it('shows the queued notice', async () => {
    get.mockResolvedValue(ai({ ai_status: 'queued' }))
    render(<EntryDetailPage />)
    expect(await screen.findByText(QUEUED)).toBeInTheDocument()
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
  })

  it('hides the notice while Regenerate runs', async () => {
    get.mockResolvedValue(ai({ ai_status: 'queued', events: [{ id: 'ev' }] as never }))
    ;(api.entries.regenerate as jest.Mock).mockResolvedValue(ai({ ai_status: 'queued' }))
    render(<EntryDetailPage />)
    expect(await screen.findByText(QUEUED)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /regenerate with ai/i }))
    await act(async () => {})
    expect(screen.queryByText(QUEUED)).not.toBeInTheDocument()
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
    expect(screen.getByText('Regenerating draft…')).toBeInTheDocument()
  })

  it('polls and shows the draft when AI finishes', async () => {
    jest.useFakeTimers()
    get
      .mockResolvedValueOnce(ai({ ai_status: 'writing', body_markdown: null }))
      .mockResolvedValueOnce(ai({ ai_status: 'writing', body_markdown: null }))
      .mockResolvedValue(ai({ ai_status: null, body_markdown: 'The finished draft' }))
    render(<EntryDetailPage />)
    await act(async () => {})
    expect(screen.getByText(WRITING)).toBeInTheDocument()
    expect(screen.queryByText('The finished draft')).not.toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(screen.getByText('The finished draft')).toBeInTheDocument()
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
  })

  it('keeps an edit in progress when AI finishes', async () => {
    jest.useFakeTimers()
    const suggestion = { title: 'S', body_markdown: 'AI text', flagged_tokens: [], body_source: 'llm', created_at: '2026-05-16T00:00:00Z' }
    get
      .mockResolvedValueOnce(ai({ ai_status: 'writing' }))
      .mockResolvedValueOnce(ai({ ai_status: 'writing' }))
      .mockResolvedValue(ai({ ai_status: null, suggestion: suggestion as never }))
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'My typed body' } })
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(screen.getByLabelText('Body')).toHaveValue('My typed body')
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('AI text')).toBeInTheDocument()
  })

  it('edit stays enabled while AI works', async () => {
    get.mockResolvedValue(ai({ ai_status: 'writing' }))
    render(<EntryDetailPage />)
    expect(await screen.findByText(WRITING)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled()
  })
  it('the notice is a status callout with an icon', async () => {
    get.mockResolvedValue(ai({ ai_status: 'writing' }))
    const { unmount } = render(<EntryDetailPage />)
    const box = await screen.findByTestId('ai-notice')
    expect(box).toHaveAttribute('role', 'status')
    expect(box).toHaveTextContent(WRITING)
    expect(box.querySelector('.spinner')).not.toBeNull()
    unmount()
    get.mockResolvedValue(ai({ ai_status: 'queued' }))
    render(<EntryDetailPage />)
    const q = await screen.findByTestId('ai-notice')
    expect(q).toHaveTextContent('⏱')
    expect(q.querySelector('.spinner')).toBeNull()
  })

  it('drops "edit anyway" for someone who cannot edit', async () => {
    get.mockResolvedValue(ai({ ai_status: 'writing', can_edit: false }))
    const { unmount } = render(<EntryDetailPage />)
    expect(await screen.findByTestId('ai-notice')).toHaveTextContent(/^AI is writing this entry\.$/)
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
    unmount()
    get.mockResolvedValue(ai({ ai_status: 'queued', can_edit: false }))
    render(<EntryDetailPage />)
    expect(await screen.findByTestId('ai-notice')).toHaveTextContent(/^⏱AI will write this entry shortly\.$/)
    expect(screen.queryByText(QUEUED)).not.toBeInTheDocument()
  })

  it('drops "edit anyway" on a read-only diary', async () => {
    get.mockResolvedValue({ ...ai({ ai_status: 'writing' }), plan: freePlan({ read_only: true }) } as Entry)
    const { unmount } = render(<EntryDetailPage />)
    expect(await screen.findByText('AI is writing this entry.')).toBeInTheDocument()
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
    unmount()
    get.mockResolvedValue({ ...ai({ ai_status: 'queued' }), plan: freePlan({ read_only: true }) } as Entry)
    render(<EntryDetailPage />)
    expect(await screen.findByText('AI will write this entry shortly.')).toBeInTheDocument()
    expect(screen.queryByText(QUEUED)).not.toBeInTheDocument()
  })

  const TIMED_OUT = 'Generation is taking longer than expected — refresh the page to check'

  it('a Regenerate still waiting after 60 s hands over to the AI notice, not "failed"', async () => {
    jest.useFakeTimers()
    let done = false
    get.mockImplementation(async () =>
      done
        ? ai({ ai_status: null, body_markdown: 'The new draft', updated_at: '2026-05-16T01:00:00Z', events: [{ id: 'ev' }] as never })
        : ai({ ai_status: 'queued', events: [{ id: 'ev' }] as never }),
    )
    get.mockResolvedValueOnce(ai({ ai_status: null, events: [{ id: 'ev' }] as never }))
    ;(api.entries.regenerate as jest.Mock).mockResolvedValue(ai({ ai_status: 'queued', events: [{ id: 'ev' }] as never }))
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /regenerate with ai/i }))
    await act(async () => {})
    expect(screen.getByText('Regenerating draft…')).toBeInTheDocument()
    await act(async () => { await jest.advanceTimersByTimeAsync(60 * 1000) })
    expect(screen.queryByText(TIMED_OUT)).not.toBeInTheDocument()
    expect(screen.queryByText(/Regenerating draft|Still working/)).not.toBeInTheDocument()
    expect(screen.getByText(QUEUED)).toBeInTheDocument()
    done = true
    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText('The new draft')).toBeInTheDocument()
    expect(screen.queryByTestId('ai-notice')).not.toBeInTheDocument()
  })

  it('a Regenerate with nothing pending after 60 s still says it is taking longer', async () => {
    jest.useFakeTimers()
    get.mockResolvedValue(ai({ ai_status: null, events: [{ id: 'ev' }] as never }))
    ;(api.entries.regenerate as jest.Mock).mockResolvedValue(ai({ ai_status: 'queued', events: [{ id: 'ev' }] as never }))
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /regenerate with ai/i }))
    await act(async () => {})
    await act(async () => { await jest.advanceTimersByTimeAsync(60 * 1000) })
    expect(screen.getByText(TIMED_OUT)).toBeInTheDocument()
    expect(screen.queryByTestId('ai-notice')).not.toBeInTheDocument()
  })

  const WARN = 'The AI draft arrived while you were editing. Save to keep your text instead, or Cancel to read the AI draft.'

  it('warns when the AI draft lands mid-edit', async () => {
    jest.useFakeTimers()
    get
      .mockResolvedValueOnce(ai({ ai_status: 'writing', body_markdown: null }))
      .mockResolvedValueOnce(ai({ ai_status: 'writing', body_markdown: null }))
      .mockResolvedValue(ai({ ai_status: null, body_markdown: 'The AI body' }))
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'My typed body' } })
    expect(screen.queryByText(WARN)).not.toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(screen.getByText(WARN)).toHaveAttribute('role', 'status')
    expect(screen.getByLabelText('Body')).toHaveValue('My typed body')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('The AI body')).toBeInTheDocument()
  })

  it('no warning when the draft lands as a suggestion', async () => {
    jest.useFakeTimers()
    const suggestion = { title: 'S', body_markdown: 'AI text', flagged_tokens: [], body_source: 'llm', created_at: '2026-05-16T00:00:00Z' }
    get
      .mockResolvedValueOnce(ai({ ai_status: 'writing' }))
      .mockResolvedValueOnce(ai({ ai_status: 'writing' }))
      .mockResolvedValue(ai({ ai_status: null, suggestion: suggestion as never }))
    render(<EntryDetailPage />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'My typed body' } })
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(screen.queryByText(WARN)).not.toBeInTheDocument()
    expect(screen.queryByText(WRITING)).not.toBeInTheDocument()
  })
})
