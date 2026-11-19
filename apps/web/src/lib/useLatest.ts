'use client'
import { useLayoutEffect, useRef, type RefObject } from 'react'

/**
 * A ref that always holds the latest committed `value`, for async work and event
 * handlers that outlive the render that started them (an upload that finishes after
 * the entry changed). It is written after commit, never during render, so a render
 * React throws away cannot leak into it (React Compiler rule `react-hooks/refs`,
 * tracker row 61). Read `.current` only outside render. In an effect, prefer
 * `useEffectEvent`.
 */
export function useLatest<T>(value: T): RefObject<T> {
  const ref = useRef(value)
  useLayoutEffect(() => {
    ref.current = value
  })
  return ref
}
