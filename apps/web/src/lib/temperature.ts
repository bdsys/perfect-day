import type { TemperatureUnit } from '@/lib/api'

// Weather is stored in °C. Round half up with floor(x + 0.5), exactly like the API,
// so the page and the AI draft agree (-0.5 -> 0, 20.5 -> 21, -1.5 -> -1).
export function toUnit(celsius: number, unit: TemperatureUnit): number {
  const v = unit === 'F' ? (celsius * 9) / 5 + 32 : celsius
  // `+ 0` turns -0 into 0 so "-0°C" can never render.
  return Math.floor(v + 0.5) + 0
}

export function formatTemp(celsius: unknown, unit: TemperatureUnit): string | null {
  if (typeof celsius !== 'number' || !Number.isFinite(celsius)) return null
  return `${toUnit(celsius, unit)}°${unit}`
}
