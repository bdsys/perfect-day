import { render, act } from '@testing-library/react'
import { StatusPanel } from '../StatusPanel'

describe('StatusPanel note', () => {
  beforeEach(() => jest.useFakeTimers())
  afterEach(() => jest.useRealTimers())

  it('keeps a success panel that carries a note until the person dismisses it', () => {
    const onDismiss = jest.fn()
    render(<StatusPanel state="success" headline="Backfill complete" note="1 event couldn't be saved." onDismiss={onDismiss} />)
    act(() => { jest.advanceTimersByTime(9000) })
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('still auto-dismisses a plain success panel', () => {
    const onDismiss = jest.fn()
    render(<StatusPanel state="success" headline="Backfill complete" onDismiss={onDismiss} />)
    act(() => { jest.advanceTimersByTime(9000) })
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})

describe('StatusPanel elapsed time (row 61)', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.setSystemTime(new Date('2026-11-19T10:00:00Z'))
  })
  afterEach(() => jest.useRealTimers())

  it('counts up every second while running', () => {
    const { getByText } = render(<StatusPanel state="running" headline="Scanning" startedAt="2026-11-19T09:59:50Z" />)
    expect(getByText(/10s ago/)).toBeInTheDocument()
    act(() => { jest.advanceTimersByTime(65_000) })
    expect(getByText(/1m 15s ago/)).toBeInTheDocument()
  })

  it('a new start time shows at once, not a second later', () => {
    const { getByText, rerender } = render(<StatusPanel state="running" headline="Scanning" startedAt="2026-11-19T09:59:50Z" />)
    rerender(<StatusPanel state="running" headline="Scanning" startedAt="2026-11-19T09:58:00Z" />)
    expect(getByText(/2m 0s ago/)).toBeInTheDocument()
  })
})
