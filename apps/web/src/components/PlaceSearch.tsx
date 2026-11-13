'use client'

import { useEffect, useState } from 'react'
import { api, ApiError, type GeocodeResult } from '@/lib/api'

interface Props {
  /** The box's accessible name, e.g. "Location" or "Home location" */
  label: string
  onSelect: (place: GeocodeResult) => void
}

const MIN_CHARS = 2
const DEBOUNCE_MS = 300

/** A city search box: type a town, pick it from the list (item 24). */
export function PlaceSearch({ label, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<GeocodeResult[] | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (q.length < MIN_CHARS) {
      setResults(null)
      setDown(false)
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api.geocode
        .search(q)
        .then((found) => {
          if (cancelled) return
          setDown(false)
          setResults(found)
        })
        .catch((e: unknown) => {
          if (cancelled) return
          setResults(null)
          setDown(e instanceof ApiError && e.status === 503)
        })
    }, DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  return (
    <div>
      <input
        type="search"
        aria-label={label}
        placeholder="Search for a town or city"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{ width: '100%' }}
      />
      {down && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
          Search isn&apos;t working right now
        </p>
      )}
      {results && results.length === 0 && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>No matches</p>
      )}
      {results && results.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0.25rem 0 0' }}>
          {results.map((place) => (
            <li key={`${place.lat},${place.lon},${place.label}`}>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ width: '100%', textAlign: 'left', marginTop: '0.25rem' }}
                onClick={() => {
                  onSelect(place)
                  setQuery('')
                  setResults(null)
                }}
              >
                {place.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
