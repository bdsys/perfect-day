import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PlaceSearch } from '../PlaceSearch'
import { api } from '../../lib/api'

jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return { ...actual, api: { geocode: { search: jest.fn() } } }
})

const { ApiError } = jest.requireActual('../../lib/api')
const search = api.geocode.search as jest.Mock

const MUKILTEO = {
  name: 'Mukilteo', admin1: 'Washington', country: 'United States',
  lat: 47.94454, lon: -122.30458, timezone: 'America/Los_Angeles',
  label: 'Mukilteo, Washington, United States',
}

function type(text: string) {
  fireEvent.change(screen.getByRole('searchbox', { name: 'Location' }), { target: { value: text } })
}

describe('PlaceSearch', () => {
  beforeEach(() => search.mockReset())

  it('waits for two characters before searching', async () => {
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)
    type('M')
    await new Promise((r) => setTimeout(r, 400))
    expect(search).not.toHaveBeenCalled()
  })

  it('lists matches by their label and hands back the chosen place', async () => {
    search.mockResolvedValue([MUKILTEO])
    const onSelect = jest.fn()
    render(<PlaceSearch label="Location" onSelect={onSelect} />)

    type('Muk')
    fireEvent.click(await screen.findByRole('button', { name: MUKILTEO.label }))

    expect(search).toHaveBeenCalledWith('Muk')
    expect(onSelect).toHaveBeenCalledWith(MUKILTEO)
  })

  it('shows Searching… while the request is pending', async () => {
    let resolve: (v: unknown[]) => void = () => {}
    search.mockReturnValue(new Promise((r) => { resolve = r }))
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)

    type('Muk')
    expect(await screen.findByText('Searching…')).toBeInTheDocument()

    resolve([MUKILTEO])
    await screen.findByRole('button', { name: MUKILTEO.label })
    expect(screen.queryByText('Searching…')).not.toBeInTheDocument()
  })

  it('says so when nothing matches', async () => {
    search.mockResolvedValue([])
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)
    type('Zzzz')
    expect(await screen.findByText('No matches')).toBeInTheDocument()
  })

  it('says search is down when the server answers 503', async () => {
    search.mockRejectedValue(new ApiError(503, 'geocoding_unavailable'))
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)
    type('Muk')
    await waitFor(() =>
      expect(screen.getByText("Search isn't working right now")).toBeInTheDocument(),
    )
  })
})

describe('PlaceSearch, row 61: behaviour kept', () => {
  beforeEach(() => search.mockReset())

  it('shortening the query below two letters clears matches, "down" and Searching… at once', async () => {
    search.mockRejectedValueOnce(new ApiError(503, 'down')).mockReturnValueOnce(new Promise(() => {}))
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)
    type('Mu')
    expect(await screen.findByText("Search isn't working right now")).toBeInTheDocument()
    type('Muk')
    expect(await screen.findByText('Searching…')).toBeInTheDocument()
    type('M')
    expect(screen.queryByText("Search isn't working right now")).toBeNull()
    expect(screen.queryByText('Searching…')).toBeNull()
    type('Mu')
    expect(screen.queryByText("Search isn't working right now")).toBeNull()
    expect(screen.queryByText('Searching…')).toBeNull()
  })

  it('picking a place empties the box and the list', async () => {
    search.mockResolvedValue([MUKILTEO])
    render(<PlaceSearch label="Location" onSelect={jest.fn()} />)
    type('Muk')
    fireEvent.click(await screen.findByRole('button', { name: MUKILTEO.label }))
    expect(screen.getByRole('searchbox', { name: 'Location' })).toHaveValue('')
    expect(screen.queryByRole('button', { name: MUKILTEO.label })).toBeNull()
  })
})
