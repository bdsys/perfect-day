export type GoogleStatus = { text: string; isError: boolean }

export type GoogleStatusParams = {
  status: string | null
  missing?: string | null
  reason?: string | null
  expected?: string | null
  /** Which page is showing the message. */
  page?: 'diaries' | 'entry'
}

/** The message for the `?google=...` result Google sends the browser back with. */
export function googleStatusMessage(p: GoogleStatusParams): GoogleStatus | null {
  const { status, missing, reason, expected, page = 'diaries' } = p
  if (!status) return null
  if (status === 'connected') {
    return page === 'entry'
      ? { text: 'Google Photos connected. You can add photos from Google Photos now.', isError: false }
      : { text: 'Google Calendar connected successfully.', isError: false }
  }
  if (status === 'partial' && missing === 'photos') return { text: "Google Photos access wasn't granted. Nothing was changed.", isError: false }
  if (status === 'partial' && missing === 'calendar') return { text: "Calendar access wasn't granted. Click Connect Google Calendar again and tick the Calendar checkbox on Google's screen.", isError: true }
  if (status === 'cancelled') return { text: 'You cancelled the Google connection. Nothing was changed.', isError: false }
  if (status === 'wrong_account') {
    return { text: `Use the Google account you connected for Calendar${expected ? ` (${expected})` : ''}.`, isError: true }
  }
  if (status === 'failed' && reason === 'browser_mismatch') {
    return { text: 'That Google link was opened in a different browser. Start again here.', isError: true }
  }
  if (status === 'failed') return { text: 'Google connection failed. Please try again.', isError: true }
  return null
}
