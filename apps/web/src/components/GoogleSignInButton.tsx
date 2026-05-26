'use client'

import { useEffect, useRef } from 'react'

interface GoogleSignInButtonProps {
  onCredential: (idToken: string) => void
}

export default function GoogleSignInButton({ onCredential }: GoogleSignInButtonProps) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
  const divRef = useRef<HTMLDivElement>(null)
  const onCredentialRef = useRef(onCredential)
  useEffect(() => { onCredentialRef.current = onCredential }, [onCredential])

  useEffect(() => {
    if (!clientId || !divRef.current) return

    // Poll for window.google to be available (GSI script loads async)
    const maxWaitMs = 3000
    const intervalMs = 100
    let elapsed = 0

    const timer = setInterval(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const google = (window as any).google
      if (google?.accounts?.id) {
        clearInterval(timer)
        google.accounts.id.initialize({
          client_id: clientId,
          callback: (response: { credential: string }) => {
            onCredentialRef.current(response.credential)
          },
        })
        if (divRef.current) {
          google.accounts.id.renderButton(divRef.current, {
            type: 'standard',
            theme: 'outline',
            size: 'large',
            text: 'continue_with',
          })
        }
      } else {
        elapsed += intervalMs
        if (elapsed >= maxWaitMs) {
          clearInterval(timer)
        }
      }
    }, intervalMs)

    return () => clearInterval(timer)
  }, [clientId])

  if (!clientId) return null

  return <div ref={divRef} style={{ width: '100%' }} />
}
