import { googleStatusMessage } from '../googleStatus'

describe('googleStatusMessage', () => {
  it('names the Calendar account on wrong_account', () => {
    expect(googleStatusMessage({ status: 'wrong_account', expected: 'a@b.com' })?.text).toBe(
      'Use the Google account you connected for Calendar (a@b.com).',
    )
  })
  it('explains a browser mismatch', () => {
    expect(googleStatusMessage({ status: 'failed', reason: 'browser_mismatch' })?.text).toBe(
      'That Google link was opened in a different browser. Start again here.',
    )
  })
  it('says photos were not granted', () => {
    expect(googleStatusMessage({ status: 'partial', missing: 'photos' })?.text).toBe(
      "Google Photos access wasn't granted. Nothing was changed.",
    )
  })
  it('connected wording depends on the page', () => {
    expect(googleStatusMessage({ status: 'connected', page: 'entry' })?.text).toBe(
      'Google Photos connected. You can add photos from Google Photos now.',
    )
    expect(googleStatusMessage({ status: 'connected' })?.text).toBe('Google Calendar connected successfully.')
  })
})
