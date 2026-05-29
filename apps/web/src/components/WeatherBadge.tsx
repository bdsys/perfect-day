import type { Enrichment } from '@/lib/api'
import { weatherIconFor } from '@/lib/weatherIcon'

interface Props {
  enrichments: Enrichment[]
}

function shortDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function roundTemp(v: unknown): string | null {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null
  return `${Math.round(v)}°`
}

export function WeatherBadge({ enrichments }: Props) {
  const weather = enrichments
    .filter((e) => e.kind === 'weather')
    .slice()
    .sort((a, b) => {
      const aKey = a.captured_for_at ?? ''
      const bKey = b.captured_for_at ?? ''
      return aKey.localeCompare(bKey)
    })

  if (weather.length === 0) return null

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.75rem',
        flexWrap: 'wrap',
        fontSize: '0.875rem',
        color: 'var(--text-muted)',
      }}
      aria-label="Weather"
    >
      {weather.map((e) => {
        const code = (e.payload as { weathercode?: number }).weathercode
        const lo = roundTemp((e.payload as { temperature_min_c?: number }).temperature_min_c)
        const hi = roundTemp((e.payload as { temperature_max_c?: number }).temperature_max_c)
        const { Icon, label } = weatherIconFor(code)
        const dateLabel = e.captured_for_at ? shortDate(e.captured_for_at) : ''
        return (
          <span
            key={e.id}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
            title={label}
          >
            {dateLabel && <span>{dateLabel}</span>}
            <Icon size={16} aria-hidden="true" />
            {lo && <span>{lo}</span>}
            {lo && hi && <span>/</span>}
            {hi && <span>{hi}</span>}
          </span>
        )
      })}
    </div>
  )
}
