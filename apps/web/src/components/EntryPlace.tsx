'use client'

import { useEffect, useRef, useState } from 'react'
import { api, type Entry, type GeocodeResult } from '@/lib/api'
import { OpenMeteoCredit } from './OpenMeteoCredit'
import { PlaceSearch } from './PlaceSearch'

interface Props {
  entry: Entry
  onUpdated: (entry: Entry) => void
}

/** "Mukilteo, Washington" in the US, "Paris, France" elsewhere — as the API names places. */
function placeName(place: GeocodeResult): string {
  const region = place.country === 'United States' ? place.admin1 : place.country
  return [place.name, region].filter(Boolean).join(', ')
}

function timeZones(current: string | null): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  const zones = intl.supportedValuesOf?.('timeZone') ?? []
  return current && !zones.includes(current) ? [current, ...zones] : zones
}

// The worker works out the place and fetches weather after a change; look
// again a few times so the page doesn't show the new place beside old weather.
const CATCH_UP_MS = [2000, 5000, 10000]

/**
 * Where the entry happened, and (for editors) changing it, plus the
 * rarely-needed per-entry time zone under "Advanced" (item 24).
 */
export function EntryPlace({ entry, onUpdated }: Props) {
  const [editingPlace, setEditingPlace] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const hasWeather = (entry.enrichments ?? []).some((e) => e.kind === 'weather')
  const [catchUpRound, setCatchUpRound] = useState(0)
  const [catchingUp, setCatchingUp] = useState(false)
  const entryId = useRef(entry.id)
  const latestOnUpdated = useRef(onUpdated)
  latestOnUpdated.current = onUpdated

  useEffect(() => {
    if (catchUpRound === 0) return
    setCatchingUp(true)
    const timers = CATCH_UP_MS.map((ms, i) =>
      setTimeout(async () => {
        try {
          latestOnUpdated.current(await api.entries.get(entryId.current))
        } catch {
          // the next look, or a reload, will catch up
        }
        if (i === CATCH_UP_MS.length - 1) setCatchingUp(false)
      }, ms),
    )
    return () => timers.forEach(clearTimeout)
  }, [catchUpRound])

  async function save(body: Parameters<typeof api.entries.patch>[1]) {
    setBusy(true)
    setError('')
    try {
      onUpdated(await api.entries.patch(entry.id, body))
      setEditingPlace(false)
      setCatchUpRound((n) => n + 1)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ margin: '0.25rem 0 0.75rem', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {entry.location && (
          <span data-testid="entry-place">
            📍 {entry.location.name ?? 'From a photo'}
          </span>
        )}
        {entry.location && !hasWeather && <OpenMeteoCredit />}
        {catchingUp && <span>Updating the place and weather…</span>}
        {entry.can_edit && (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem' }}
            onClick={() => setEditingPlace((v) => !v)}
            disabled={busy}
          >
            Edit location
          </button>
        )}
      </div>

      {editingPlace && (
        <div className="card" style={{ marginTop: '0.5rem' }}>
          <PlaceSearch
            label="Location"
            onSelect={(place) => save({ location: { name: placeName(place), lat: place.lat, lon: place.lon } })}
          />
          {entry.location?.source === 'parent' && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ marginTop: '0.5rem' }}
              onClick={() => save({ location: null })}
              disabled={busy}
            >
              Use automatic location
            </button>
          )}
        </div>
      )}

      {entry.can_edit && (
        <details style={{ marginTop: '0.5rem' }}>
          <summary style={{ cursor: 'pointer' }}>Advanced</summary>
          <div className="form-field" style={{ marginTop: '0.5rem' }}>
            <label className="form-label" htmlFor="entry-time-zone">Time zone</label>
            <select
              id="entry-time-zone"
              value={entry.tz_override ?? ''}
              onChange={(e) => save({ tz_override: e.target.value || null })}
              disabled={busy}
            >
              <option value="">Diary time zone</option>
              {timeZones(entry.tz_override).map((zone) => (
                <option key={zone} value={zone}>{zone}</option>
              ))}
            </select>
            <p style={{ fontSize: '0.8rem', marginTop: '0.25rem' }}>
              For a day in another time zone: event times show in that zone, and the date follows them.
            </p>
          </div>
        </details>
      )}

      {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
    </div>
  )
}
