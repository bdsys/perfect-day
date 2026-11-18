import { render, screen } from '@testing-library/react'
import LoginPage from '../page'

let mockSearch = ''
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}))
jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: null, loading: false, login: jest.fn(), loginWithGoogle: jest.fn() }),
}))
jest.mock('@/components/GoogleSignInButton', () => ({ __esModule: true, default: () => null }))

describe('LoginPage deletion notice', () => {
  it('shows the deletion notice', () => {
    mockSearch = 'deleted=2026-11-24T17:04:00Z'
    render(<LoginPage />)
    expect(
      screen.getByText('Your account will be deleted on Nov 24 at 9:04 AM. Log in before then to restore it.'),
    ).toBeInTheDocument()
  })

  it('ignores a value that is not a date', () => {
    mockSearch = 'deleted=banana'
    render(<LoginPage />)
    expect(screen.queryByText(/will be deleted/)).not.toBeInTheDocument()
  })

  it('shows no notice without the parameter', () => {
    mockSearch = ''
    render(<LoginPage />)
    expect(screen.queryByText(/will be deleted/)).not.toBeInTheDocument()
  })
})
