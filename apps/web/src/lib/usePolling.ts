'use client'
import { useEffect, useRef } from 'react'

export function usePolling(
  fn: () => Promise<void>,
  intervalMs: number,
  enabled: boolean,
): void {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (!enabled) return
    let mounted = true
    const wrappedFn = () => { if (mounted) fnRef.current() }
    wrappedFn()
    const id = setInterval(wrappedFn, intervalMs)
    return () => {
      mounted = false
      clearInterval(id)
    }
  }, [enabled, intervalMs])
}
