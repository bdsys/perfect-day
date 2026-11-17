import { formatTemp, toUnit } from '../temperature'

describe('toUnit (same table as the API, rounded half up)', () => {
  it.each([
    [21.5, 'F', 71],
    [-17.5, 'F', 1],
    [20.5, 'C', 21],
    [-0.5, 'C', 0],
    [-1.5, 'C', -1],
  ] as const)('toUnit(%p,%p)=%p', (c, unit, want) => {
    expect(toUnit(c, unit)).toBe(want)
  })

  it('never returns negative zero', () => {
    expect(Object.is(toUnit(-0.5, 'C'), 0)).toBe(true)
  })
})

describe('formatTemp', () => {
  it('adds the unit symbol', () => {
    expect(formatTemp(21.5, 'F')).toBe('71°F')
    expect(formatTemp(21, 'C')).toBe('21°C')
  })
  it('renders -0.5 °C as 0°C', () => {
    expect(formatTemp(-0.5, 'C')).toBe('0°C')
  })
  it('is null for a non-number', () => {
    expect(formatTemp(undefined, 'F')).toBeNull()
    expect(formatTemp('x', 'F')).toBeNull()
    expect(formatTemp(NaN, 'F')).toBeNull()
  })
})
