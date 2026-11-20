'use client'
import { useEffect, useEffectEvent } from 'react'

export function usePolling(
  fn: () => Promise<void>,
  intervalMs: number,
  enabled: boolean,
): void {
  // Always the latest fn, without restarting the timer when it changes.
  const poll = useEffectEvent(() => { fn() })

  useEffect(() => {
    if (!enabled) return
    poll()
    const id = setInterval(poll, intervalMs)
    return () => clearInterval(id)
  }, [enabled, intervalMs])
}
