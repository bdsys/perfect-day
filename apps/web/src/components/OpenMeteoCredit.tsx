/**
 * Open-Meteo's data is CC BY 4.0: a link is required wherever its weather or
 * location data is shown (https://open-meteo.com/en/licence).
 */
export function OpenMeteoCredit() {
  return (
    <a
      href="https://open-meteo.com/"
      target="_blank"
      rel="noopener noreferrer"
      style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}
    >
      Location and weather: Open-Meteo
    </a>
  )
}
