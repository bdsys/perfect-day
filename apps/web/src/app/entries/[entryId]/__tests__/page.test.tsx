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
jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false }),
}))
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useParams: () => ({ entryId: 'e1' }),
  useSearchParams: () => new URLSearchParams(),
}))

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
})
