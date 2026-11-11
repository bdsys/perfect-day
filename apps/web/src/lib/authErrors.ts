import { ApiError } from '@/lib/api'

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Incorrect email or password.',
  email_taken: 'An account with that email already exists. Try signing in instead.',
  account_unavailable: 'This account is scheduled for deletion and can no longer sign in.',
  link_required:
    'An account with this email already exists. Sign in with your password to use it.',
  invalid_google_token: 'Google sign-in failed. Please try again.',
  validation_error: 'Please check the email address and use a password of at least 8 characters.',
}

/** Human-readable text for an error thrown by the sign-in and registration calls. */
export function authErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.code && MESSAGES[err.code]) return MESSAGES[err.code]
    if (err.status === 429) return 'Too many attempts. Wait a minute and try again.'
  }
  return fallback
}
