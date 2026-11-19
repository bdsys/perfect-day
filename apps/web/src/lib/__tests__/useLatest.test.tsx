import { renderHook } from '@testing-library/react'
import { useLatest } from '../useLatest'

it('holds the latest committed value and keeps the same ref', () => {
  const { result, rerender } = renderHook(({ v }) => useLatest(v), { initialProps: { v: 1 } })
  const ref = result.current
  expect(ref.current).toBe(1)
  rerender({ v: 2 })
  expect(result.current).toBe(ref)
  expect(ref.current).toBe(2)
})
