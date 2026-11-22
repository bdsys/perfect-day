import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import LoginPage from '../page'

let mockSearch = ''
const mockPush = jest.fn()
const mockLogin = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}))
jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: null, loading: false, login: mockLogin, loginWithGoogle: jest.fn() }),
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

describe('LoginPage return path', () => {
  it('goes to next after login', async () => {
    mockSearch = 'next=/invite/tok'
    mockLogin.mockResolvedValue(undefined)
    render(<LoginPage />)
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/invite/tok'))
  })

  it('carries next to the register link', () => {
    mockSearch = 'next=/invite/tok'
    render(<LoginPage />)
    expect(screen.getByRole('link', { name: 'Register' })).toHaveAttribute('href', '/register?next=%2Finvite%2Ftok')
  })
})
