import { render, screen } from '@testing-library/react'
import { WeatherBadge } from '../WeatherBadge'

const mkEnrichment = (date: string, code: number, lo: number, hi: number) => ({
  id: `e-${date}`,
  kind: 'weather',
  source: 'open_meteo' as string | null,
  captured_for_at: `${date}T00:00:00Z`,
  fetched_at: `${date}T01:00:00Z`,
  payload: {
    date,
    weathercode: code,
    temperature_min_c: lo,
    temperature_max_c: hi,
  },
})

describe('WeatherBadge', () => {
  it('renders nothing when no enrichments', () => {
    const { container } = render(<WeatherBadge enrichments={[]} unit="F" />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when only non-weather enrichments', () => {
    const { container } = render(
      <WeatherBadge enrichments={[{ ...mkEnrichment('2026-05-05', 0, 14, 22), kind: 'spotify' }]} unit="F" />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders temperature values for single-day entry', () => {
    render(<WeatherBadge enrichments={[mkEnrichment('2026-05-05', 0, 14, 22)]} unit="C" />)
    expect(screen.getByText('14°C')).toBeInTheDocument()
    expect(screen.getByText('22°C')).toBeInTheDocument()
  })

  it('shows °F, converted and rounded half up', () => {
    render(<WeatherBadge enrichments={[mkEnrichment('2026-05-05', 0, 16, 21)]} unit="F" />)
    expect(screen.getByText('61°F')).toBeInTheDocument()
    expect(screen.getByText('/')).toBeInTheDocument()
    expect(screen.getByText('70°F')).toBeInTheDocument()
  })

  it('shows °C unchanged', () => {
    render(<WeatherBadge enrichments={[mkEnrichment('2026-05-05', 0, 16, 21)]} unit="C" />)
    expect(screen.getByText('16°C')).toBeInTheDocument()
    expect(screen.getByText('21°C')).toBeInTheDocument()
  })

  it('renders in chronological order for multi-day entry', () => {
    const items = [
      mkEnrichment('2026-05-07', 63, 12, 18),
      mkEnrichment('2026-05-05', 0, 14, 22),
      mkEnrichment('2026-05-06', 2, 13, 20),
    ]
    render(<WeatherBadge enrichments={items} unit="C" />)
    const text = document.body.textContent ?? ''
    const i5 = text.indexOf('22°C')
    const i6 = text.indexOf('20°C')
    const i7 = text.indexOf('18°C')
    expect(i5).toBeGreaterThan(-1)
    expect(i6).toBeGreaterThan(-1)
    expect(i7).toBeGreaterThan(-1)
    expect(i5).toBeLessThan(i6)
    expect(i6).toBeLessThan(i7)
  })

  it('has aria-label="Weather" for accessibility', () => {
    render(<WeatherBadge enrichments={[mkEnrichment('2026-05-05', 0, 14, 22)]} unit="C" />)
    expect(screen.getByLabelText('Weather')).toBeTruthy()
  })
})

describe('WeatherBadge credit', () => {
  it('links to Open-Meteo, as its licence requires', () => {
    render(<WeatherBadge enrichments={[mkEnrichment('2026-05-05', 0, 14, 22)]} unit="C" />)
    const credit = screen.getByRole('link', { name: 'Location and weather: Open-Meteo' })
    expect(credit).toHaveAttribute('href', 'https://open-meteo.com/')
  })
})
