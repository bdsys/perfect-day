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
