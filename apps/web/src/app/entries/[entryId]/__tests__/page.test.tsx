import { render, screen, fireEvent, act } from '@testing-library/react'
import EntryDetailPage from '../page'
import { api, type Entry, type PhotoImport } from '@/lib/api'

jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('@/components/PhotoThumbnail', () => ({ PhotoThumbnail: () => <span data-testid="thumb" /> }))
jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      entries: { get: jest.fn() },
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
