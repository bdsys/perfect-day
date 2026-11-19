'use client'
import { useEffect, useEffectEvent } from 'react'

/**
 * Safari restores a page from its back/forward cache with its state intact. After
 * a Back press from Google, a "connecting" guard would still be set, so call `reset`
 * when the page is restored this way.
 */
export function useResetOnBfcacheRestore(reset: () => void): void {
  const onRestore = useEffectEvent(() => reset())

  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) onRestore()
    }
    window.addEventListener('pageshow', onPageShow)
    return () => window.removeEventListener('pageshow', onPageShow)
  }, [])
}
