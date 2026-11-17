'use client'

/**
 * Opened in a new tab by "Search Google Photos for <date>" on an entry. It asks the API for a
 * Google Photos picker session and then replaces itself with Google's picker.
 *
 * Why a page of our own: iOS Chrome ignored the old way (open a blank tab inside the tap,
 * then send it to Google once the session existed). A tab moving itself is always allowed.
 * The entry's tab watches for the import this creates (PhotoImportNotice `watching`).
 */
import { Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { api, ApiError } from '@/lib/api'
import { limitMessage } from '@/lib/plan'
import { replaceLocation } from '@/lib/navigate'

const SLOW_MS = 5_000
const SLOW_TEXT = 'Google Photos is loading. This can take a minute or two. Please hang with us while we wait for Google...'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function startError(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return "Google Photos isn't connected yet. Close this tab and use Connect Google Photos on the entry."
  }
  if (e instanceof ApiError && e.status === 503 && e.code === 'google_photos_not_enabled') {
    return "Google Photos isn't set up for this app yet. The app's owner needs to turn on the Photos Picker API in Google Cloud."
  }
  if (e instanceof ApiError && e.status === 503) {
    return "Google Photos isn't responding. Close this tab and try again in a minute."
  }
  return limitMessage(e) ?? "Couldn't start Google Photos. Close this tab and try again."
}

function OpenGooglePhotos() {
  const { user, loading } = useAuth()
  const entryId = useSearchParams().get('entry') ?? ''
  const validEntry = UUID.test(entryId)
  const [error, setError] = useState<string | null>(null)
  const startedRef = useRef(false) // one session, even when React runs effects twice
  const [slow, setSlow] = useState(false)

  // Our part takes about 3 s; on a phone, Google's picker can then take a minute or two to
  // load the first time, while this page stays on screen (prod logs, 2026-11-17).
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), SLOW_MS)
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    if (loading || !user || !validEntry || startedRef.current) return
    startedRef.current = true
    api.photoImports
      .create(entryId)
      .then((imp) => replaceLocation(`${imp.picker_uri}/autoclose`))
      .catch((e) => setError(startError(e)))
  }, [loading, user, validEntry, entryId])

  let message: string | null = error
  if (!validEntry) message = "This link doesn't say which entry to add photos to. Close this tab and try again."
  else if (!loading && !user) message = 'Sign in to Perfect Day in this browser, then try again.'

  return (
    <div className="container" style={{ paddingTop: '2rem' }}>
      {message ? <p role="alert" className="error-message">{message}</p> : <p>{slow ? SLOW_TEXT : 'Opening Google Photos…'}</p>}
    </div>
  )
}

export default function GooglePhotosOpenPage() {
  return (
    <Suspense fallback={<p className="container" style={{ paddingTop: '2rem' }}>Opening Google Photos…</p>}>
      <OpenGooglePhotos />
    </Suspense>
  )
}
