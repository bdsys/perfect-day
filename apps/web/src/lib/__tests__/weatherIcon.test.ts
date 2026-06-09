import {
  Sun,
  CloudSun,
  CloudRain,
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudSnow,
  CloudLightning,
  HelpCircle,
} from 'lucide-react'
import { weatherIconFor } from '../weatherIcon'

describe('weatherIconFor', () => {
  it('maps clear sky (code 0) to Sun', () => {
    const { Icon, label } = weatherIconFor(0)
    expect(Icon).toBe(Sun)
    expect(label).toBe('clear sky')
  })

  it('maps mainly clear (code 1) to Sun', () => {
    const { Icon, label } = weatherIconFor(1)
    expect(Icon).toBe(Sun)
    expect(label).toBe('mainly clear')
  })

  it('maps partly cloudy (code 2) to CloudSun', () => {
    const { Icon, label } = weatherIconFor(2)
    expect(Icon).toBe(CloudSun)
    expect(label).toBe('partly cloudy')
  })

  it('maps overcast (code 3) to Cloud', () => {
    const { Icon, label } = weatherIconFor(3)
    expect(Icon).toBe(Cloud)
    expect(label).toBe('overcast')
  })

  it('maps fog (code 45) to CloudFog', () => {
    const { Icon, label } = weatherIconFor(45)
    expect(Icon).toBe(CloudFog)
    expect(label).toBe('fog')
  })

  it('maps depositing rime fog (code 48) to CloudFog', () => {
    const { Icon, label } = weatherIconFor(48)
    expect(Icon).toBe(CloudFog)
    expect(label).toBe('depositing rime fog')
  })

  it('maps light drizzle (code 51) to CloudDrizzle', () => {
    const { Icon, label } = weatherIconFor(51)
    expect(Icon).toBe(CloudDrizzle)
    expect(label).toBe('light drizzle')
  })

  it('maps moderate drizzle (code 53) to CloudDrizzle', () => {
    const { Icon, label } = weatherIconFor(53)
    expect(Icon).toBe(CloudDrizzle)
    expect(label).toBe('moderate drizzle')
  })

  it('maps dense drizzle (code 55) to CloudDrizzle', () => {
    const { Icon, label } = weatherIconFor(55)
    expect(Icon).toBe(CloudDrizzle)
    expect(label).toBe('dense drizzle')
  })

  it('maps slight rain (code 61) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(61)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('slight rain')
  })

  it('maps moderate rain (code 63) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(63)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('moderate rain')
  })

  it('maps heavy rain (code 65) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(65)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('heavy rain')
  })

  it('maps moderate snow (code 71) to CloudSnow', () => {
    const { Icon, label } = weatherIconFor(71)
    expect(Icon).toBe(CloudSnow)
    expect(label).toBe('moderate snow')
  })

  it('maps heavy snow (code 73) to CloudSnow', () => {
    const { Icon, label } = weatherIconFor(73)
    expect(Icon).toBe(CloudSnow)
    expect(label).toBe('heavy snow')
  })

  it('maps snowfall (code 75) to CloudSnow', () => {
    const { Icon, label } = weatherIconFor(75)
    expect(Icon).toBe(CloudSnow)
    expect(label).toBe('snowfall')
  })

  it('maps rain showers (code 80) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(80)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('rain showers')
  })

  it('maps moderate rain showers (code 81) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(81)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('moderate rain showers')
  })

  it('maps violent rain showers (code 82) to CloudRain', () => {
    const { Icon, label } = weatherIconFor(82)
    expect(Icon).toBe(CloudRain)
    expect(label).toBe('violent rain showers')
  })

  it('maps thunderstorm (code 95) to CloudLightning', () => {
    const { Icon, label } = weatherIconFor(95)
    expect(Icon).toBe(CloudLightning)
    expect(label).toBe('thunderstorm')
  })

  it('maps thunderstorm with hail (code 96) to CloudLightning', () => {
    const { Icon, label } = weatherIconFor(96)
    expect(Icon).toBe(CloudLightning)
    expect(label).toBe('thunderstorm with hail')
  })

  it('maps thunderstorm with heavy hail (code 99) to CloudLightning', () => {
    const { Icon, label } = weatherIconFor(99)
    expect(Icon).toBe(CloudLightning)
    expect(label).toBe('thunderstorm with heavy hail')
  })

  it('returns a fallback for unknown codes', () => {
    const { Icon, label } = weatherIconFor(999)
    expect(Icon).toBe(HelpCircle)
    expect(label).toBe('unknown')
  })

  it('handles null gracefully', () => {
    const { Icon, label } = weatherIconFor(null)
    expect(Icon).toBe(HelpCircle)
    expect(label).toBe('unknown')
  })

  it('handles undefined gracefully', () => {
    const { Icon, label } = weatherIconFor(undefined)
    expect(Icon).toBe(HelpCircle)
    expect(label).toBe('unknown')
  })
})
