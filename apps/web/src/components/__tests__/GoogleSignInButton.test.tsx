import { act, render } from '@testing-library/react'

let mockScriptProps: { src?: string; onReady?: () => void }[] = []
jest.mock('next/script', () => ({
  __esModule: true,
  default: (props: { src?: string; onReady?: () => void }) => {
    mockScriptProps.push(props)
    return <script data-testid="next-script" data-src={props.src} />
  },
}))

import GoogleSignInButton from '../GoogleSignInButton'

const GSI = 'https://accounts.google.com/gsi/client'

beforeEach(() => {
  mockScriptProps = []
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'client-123'
})
afterEach(() => {
  delete process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (window as any).google
})

it('loads Google sign-in itself (the root layout no longer does)', () => {
  const { container } = render(<GoogleSignInButton onCredential={jest.fn()} />)
  expect(container.querySelector(`script[data-src="${GSI}"]`)).not.toBeNull()
})

it('renders the Google button once the script is ready', () => {
  const initialize = jest.fn()
  const renderButton = jest.fn()
  const onCredential = jest.fn()
  render(<GoogleSignInButton onCredential={onCredential} />)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(window as any).google = { accounts: { id: { initialize, renderButton } } }
  act(() => mockScriptProps.at(-1)?.onReady?.())
  expect(initialize).toHaveBeenCalledWith(expect.objectContaining({ client_id: 'client-123' }))
  expect(renderButton).toHaveBeenCalledTimes(1)
  initialize.mock.calls[0][0].callback({ credential: 'id-token' })
  expect(onCredential).toHaveBeenCalledWith('id-token')
})

it('renders nothing and loads nothing without a client id', () => {
  delete process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
  const { container } = render(<GoogleSignInButton onCredential={jest.fn()} />)
  expect(container.innerHTML).toBe('')
})
