import { renderHook } from '@testing-library/react'
import { useResetOnBfcacheRestore } from '../useResetOnBfcacheRestore'

function pageshow(persisted: boolean) {
  const event = new Event('pageshow') as PageTransitionEvent
  Object.defineProperty(event, 'persisted', { value: persisted })
  window.dispatchEvent(event)
}

it('calls the latest reset only when Safari restores the page from its cache', () => {
  const first = jest.fn()
  const second = jest.fn()
  const { rerender, unmount } = renderHook(({ reset }) => useResetOnBfcacheRestore(reset), { initialProps: { reset: first } })
  pageshow(false)
  expect(first).not.toHaveBeenCalled()
  rerender({ reset: second })
  pageshow(true)
  expect(first).not.toHaveBeenCalled()
  expect(second).toHaveBeenCalledTimes(1)
  unmount()
  pageshow(true)
  expect(second).toHaveBeenCalledTimes(1)
})
