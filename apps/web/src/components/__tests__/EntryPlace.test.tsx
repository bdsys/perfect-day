import { render, screen, fireEvent, act } from '@testing-library/react'
import { EntryPlace } from '../EntryPlace'
import { api, ApiError, type Entry } from '../../lib/api'

jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: { entries: { patch: jest.fn(), get: jest.fn() }, geocode: { search: jest.fn() } },
  }
})

const patch = api.entries.patch as jest.Mock
const get = api.entries.get as jest.Mock

function entry(over: Partial<Entry> = {}): Entry {
  return {
    id: 'e1', diary_id: 'd1', entry_date: '2026-05-16', entry_end_date: null, title: 'T',
    body_markdown: 'B', body_source: 'user', flagged_tokens: null, status: 'draft',
    created_by: 'manual', creation_source: 'manual', published_at: null, deleted_at: null,
    created_at: '', updated_at: '', events: [], rule_matches: [], last_generation: null,
    photos: [], enrichments: [], suggestion: null, ai_status: null, tz_override: null, can_edit: true,
    location: { name: 'Mukilteo, Washington', lat: null, lon: null, source: 'parent' },
    ...over,
  }
}

describe('EntryPlace after a change', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    patch.mockReset()
    get.mockReset()
  })
  afterEach(() => jest.useRealTimers())

  it('fetches the entry again while the weather catches up (final review I5)', async () => {
    const saved = entry({ location: null })
    const caughtUp = entry({ location: { name: 'Everett, Washington', lat: null, lon: null, source: 'home' } })
    patch.mockResolvedValue(saved)
    get.mockResolvedValue(caughtUp)
    const onUpdated = jest.fn()
    render(<EntryPlace entry={entry()} onUpdated={onUpdated} />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit location' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Use automatic location' }))
    })
    expect(onUpdated).toHaveBeenLastCalledWith(saved)
    expect(screen.getByText('Updating the place and weather…')).toBeInTheDocument()

    await act(async () => {
      await jest.advanceTimersByTimeAsync(12_000)
    })

    expect(get).toHaveBeenCalledWith('e1')
    expect(onUpdated).toHaveBeenLastCalledWith(caughtUp)
    expect(screen.queryByText('Updating the place and weather…')).not.toBeInTheDocument()
  })
})

describe('EntryPlace label for an unnamed place (TR-24)', () => {
  it.each([
    ['photo', 'From a photo'],
    ['home', 'Home'],
    ['event', 'From the event'],
    ['parent', 'Your place'],
  ] as const)('reads "%s" as "%s"', (source, text) => {
    render(
      <EntryPlace
        entry={entry({ location: { name: null, lat: null, lon: null, source } })}
        onUpdated={jest.fn()}
      />,
    )
    expect(screen.getByTestId('entry-place')).toHaveTextContent(text)
  })
})

describe('EntryPlace refused save', () => {
  it('shows mapped copy for a tier_limit, never the raw code', async () => {
    patch.mockReset()
    patch.mockRejectedValue(new ApiError(403, 'tier_limit', 'tier_limit', { source: 'diary_read_only' }))
    render(<EntryPlace entry={entry()} onUpdated={jest.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit location' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Use automatic location' }))
    })
    expect(screen.getByText(/This diary is read-only/)).toBeInTheDocument()
    expect(screen.queryByText(/tier_limit/)).toBeNull()
  })
})

describe('EntryPlace on a read-only diary', () => {
  it('disables Edit location', () => {
    render(<EntryPlace entry={entry()} onUpdated={jest.fn()} readOnly />)
    expect(screen.getByRole('button', { name: 'Edit location' })).toBeDisabled()
  })
})
