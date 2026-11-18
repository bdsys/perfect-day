import { ApiError } from '@/lib/api'
import { authErrorMessage } from '@/lib/authErrors'

describe('authErrorMessage', () => {
  it('says an unavailable account is no longer available', () => {
    const err = new ApiError(401, 'x', 'account_unavailable')
    expect(authErrorMessage(err, 'fallback')).toBe('This account is no longer available.')
  })
})
