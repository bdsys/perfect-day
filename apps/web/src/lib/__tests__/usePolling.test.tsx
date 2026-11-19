import { renderHook, act } from '@testing-library/react'
import { usePolling } from '../usePolling'

beforeEach(() => jest.useFakeTimers())
afterEach(() => jest.useRealTimers())

it('calls at once, then every interval', () => {
  const fn = jest.fn().mockResolvedValue(undefined)
  renderHook(() => usePolling(fn, 1000, true))
  expect(fn).toHaveBeenCalledTimes(1)
  act(() => { jest.advanceTimersByTime(2000) })
  expect(fn).toHaveBeenCalledTimes(3)
})

it('calls the latest function without restarting the timer', () => {
  const first = jest.fn().mockResolvedValue(undefined)
  const second = jest.fn().mockResolvedValue(undefined)
  const { rerender } = renderHook(({ fn }) => usePolling(fn, 1000, true), { initialProps: { fn: first } })
  act(() => { jest.advanceTimersByTime(500) })
  rerender({ fn: second })
  expect(second).not.toHaveBeenCalled() // a new function alone does not poll early
  act(() => { jest.advanceTimersByTime(500) })
  expect(first).toHaveBeenCalledTimes(1)
  expect(second).toHaveBeenCalledTimes(1)
})

it('does nothing while disabled, and stops when disabled or unmounted', () => {
  const fn = jest.fn().mockResolvedValue(undefined)
  const { rerender, unmount } = renderHook(({ on }) => usePolling(fn, 1000, on), { initialProps: { on: false } })
  act(() => { jest.advanceTimersByTime(3000) })
  expect(fn).not.toHaveBeenCalled()
  rerender({ on: true })
  expect(fn).toHaveBeenCalledTimes(1)
  rerender({ on: false })
  act(() => { jest.advanceTimersByTime(3000) })
  expect(fn).toHaveBeenCalledTimes(1)
  rerender({ on: true })
  unmount()
  act(() => { jest.advanceTimersByTime(3000) })
  expect(fn).toHaveBeenCalledTimes(2)
})
