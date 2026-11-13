import { render, screen, fireEvent, act } from '@testing-library/react'
import { EntryPlace } from '../EntryPlace'
import { api, type Entry } from '../../lib/api'

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
    photos: [], enrichments: [], suggestion: null, tz_override: null, can_edit: true,
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
