import { render, screen, fireEvent } from '@testing-library/react'
import AppHeader from '@/components/AppHeader'
import { navigateTo } from '@/lib/navigate'

const logout = jest.fn().mockResolvedValue(undefined)
let mockAuth: Record<string, unknown>

jest.mock('@/lib/auth-context', () => ({ useAuth: () => mockAuth }))
jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn() }))

beforeEach(() => {
  logout.mockClear()
  ;(navigateTo as jest.Mock).mockClear()
  mockAuth = {
    user: { id: 'u1', email: 'andrew@example.com', avatar: { kind: 'initial' } },
    avatarUrl: null,
    logout,
  }
})

it('shows the back link and the brand', () => {
  render(<AppHeader back={{ href: '/diaries', label: '← Diaries' }} />)
  expect(screen.getByRole('link', { name: 'Perfect Day' })).toHaveAttribute('href', '/diaries')
  expect(screen.getByRole('link', { name: '← Diaries' })).toHaveAttribute('href', '/diaries')
})

it('renders with partial auth objects', () => {
  mockAuth = { user: { id: 'u1' }, logout }
  render(<AppHeader />)
  expect(screen.getByRole('button', { name: 'Account menu' })).toHaveTextContent('?')
})

it('has no avatar button without a user', () => {
  mockAuth = { user: null, logout }
  render(<AppHeader />)
  expect(screen.getByRole('link', { name: 'Perfect Day' })).toBeInTheDocument()
  expect(screen.queryByRole('button')).toBeNull()
})

it('menu opens, Escape closes and refocuses the button', () => {
  render(<AppHeader />)
  const btn = screen.getByRole('button', { name: 'Account menu' })
  expect(btn).toHaveAttribute('aria-haspopup', 'menu')
  expect(screen.queryByRole('menu')).toBeNull()
  fireEvent.click(btn)
  expect(screen.getByRole('menu')).toBeInTheDocument()
  expect(screen.getByRole('menuitem', { name: 'Profile' })).toHaveAttribute('href', '/profile')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('menu')).toBeNull()
  expect(btn).toHaveFocus()
})

it('arrow keys move between the items', () => {
  render(<AppHeader />)
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }))
  const profile = screen.getByRole('menuitem', { name: 'Profile' })
  const signOut = screen.getByRole('menuitem', { name: 'Sign out' })
  expect(profile).toHaveFocus()
  fireEvent.keyDown(profile, { key: 'ArrowDown' })
  expect(signOut).toHaveFocus()
  fireEvent.keyDown(signOut, { key: 'ArrowDown' })
  expect(profile).toHaveFocus()
  fireEvent.keyDown(profile, { key: 'ArrowUp' })
  expect(signOut).toHaveFocus()
})

it('outside click closes; aria-expanded tracks it', () => {
  render(<AppHeader />)
  const btn = screen.getByRole('button', { name: 'Account menu' })
  expect(btn).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(btn)
  expect(btn).toHaveAttribute('aria-expanded', 'true')
  fireEvent.mouseDown(document.body)
  expect(screen.queryByRole('menu')).toBeNull()
  expect(btn).toHaveAttribute('aria-expanded', 'false')
})

it('Sign out logs out and goes to /login', async () => {
  render(<AppHeader />)
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }))
  await Promise.resolve()
  await Promise.resolve()
  expect(logout).toHaveBeenCalled()
  expect(navigateTo).toHaveBeenCalledWith('/login')
})
