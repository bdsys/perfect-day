'use client'

import Script from 'next/script'
import { useCallback, useEffect, useRef } from 'react'

// Loaded here, not in the root layout, so pages whose URL is a secret (/p/{token})
// never load Google's script. Next loads a <Script> src once however often it mounts.
const GSI_SRC = 'https://accounts.google.com/gsi/client'

interface GoogleSignInButtonProps {
  onCredential: (idToken: string) => void
}

export default function GoogleSignInButton({ onCredential }: GoogleSignInButtonProps) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
  const divRef = useRef<HTMLDivElement>(null)
  const onCredentialRef = useRef(onCredential)
  const renderedRef = useRef(false)
  useEffect(() => { onCredentialRef.current = onCredential }, [onCredential])

  // Renders Google's button once window.google is there; true when it has.
  const tryRender = useCallback((): boolean => {
    if (renderedRef.current) return true
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const google = (window as any).google
    if (!clientId || !divRef.current || !google?.accounts?.id) return false
    google.accounts.id.initialize({
      client_id: clientId,
      callback: (response: { credential: string }) => {
        onCredentialRef.current(response.credential)
      },
    })
    google.accounts.id.renderButton(divRef.current, {
      type: 'standard',
      theme: 'outline',
      size: 'large',
      text: 'continue_with',
    })
    renderedRef.current = true
    return true
  }, [clientId])

  useEffect(() => {
    if (!clientId) return

    // Poll for window.google too: onReady covers a load slower than this.
    const maxWaitMs = 3000
    const intervalMs = 100
    let elapsed = 0

    const timer = setInterval(() => {
      elapsed += intervalMs
      if (tryRender() || elapsed >= maxWaitMs) clearInterval(timer)
    }, intervalMs)

    return () => clearInterval(timer)
  }, [clientId, tryRender])

  if (!clientId) return null

  return (
    <>
      <Script src={GSI_SRC} strategy="afterInteractive" onReady={() => { tryRender() }} />
      <div ref={divRef} style={{ width: '100%' }} />
    </>
  )
}
