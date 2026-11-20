'use client'
import { useEffect, useState } from 'react'
import { api } from './api'

type Loaded = { photoId: string; src: string | null; error: boolean }

/**
 * Fetches and decrypts one photo as an object URL, revoked when the photo changes or
 * the component goes away. Each result is tagged with its photo, so after a change the
 * caller sees "loading" in the same render, never the previous photo's revoked URL or
 * its error (tracker row 61).
 */
export function usePhotoBlob(photoId: string, kind: 'full' | 'thumb'): { src: string | null; error: boolean } {
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    let url: string | null = null
    let cancelled = false
    api.photos.get(photoId, kind).then((blob) => {
      if (cancelled) return
      url = URL.createObjectURL(blob)
      setLoaded({ photoId, src: url, error: false })
    }).catch(() => {
      if (!cancelled) setLoaded({ photoId, src: null, error: true })
    })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [photoId, kind])

  return loaded?.photoId === photoId ? { src: loaded.src, error: loaded.error } : { src: null, error: false }
}
