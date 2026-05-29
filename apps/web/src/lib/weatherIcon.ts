import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  HelpCircle,
  Sun,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export interface WeatherIconResult {
  Icon: LucideIcon
  label: string
}

// Mirrors apps/api/app/workers/open_meteo.py:_WEATHERCODE_LABELS
const MAP: Record<number, WeatherIconResult> = {
  0: { Icon: Sun, label: 'clear sky' },
  1: { Icon: Sun, label: 'mainly clear' },
  2: { Icon: CloudSun, label: 'partly cloudy' },
  3: { Icon: Cloud, label: 'overcast' },
  45: { Icon: CloudFog, label: 'fog' },
  48: { Icon: CloudFog, label: 'depositing rime fog' },
  51: { Icon: CloudDrizzle, label: 'light drizzle' },
  53: { Icon: CloudDrizzle, label: 'moderate drizzle' },
  55: { Icon: CloudDrizzle, label: 'dense drizzle' },
  61: { Icon: CloudRain, label: 'slight rain' },
  63: { Icon: CloudRain, label: 'moderate rain' },
  65: { Icon: CloudRain, label: 'heavy rain' },
  71: { Icon: CloudSnow, label: 'moderate snow' },
  73: { Icon: CloudSnow, label: 'heavy snow' },
  75: { Icon: CloudSnow, label: 'snowfall' },
  80: { Icon: CloudRain, label: 'rain showers' },
  81: { Icon: CloudRain, label: 'moderate rain showers' },
  82: { Icon: CloudRain, label: 'violent rain showers' },
  95: { Icon: CloudLightning, label: 'thunderstorm' },
  96: { Icon: CloudLightning, label: 'thunderstorm with hail' },
  99: { Icon: CloudLightning, label: 'thunderstorm with heavy hail' },
}

export function weatherIconFor(
  code: number | null | undefined,
): WeatherIconResult {
  if (code == null) return { Icon: HelpCircle, label: 'unknown' }
  return MAP[code] ?? { Icon: HelpCircle, label: 'unknown' }
}
