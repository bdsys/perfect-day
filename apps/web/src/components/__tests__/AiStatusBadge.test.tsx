import { render, screen } from '@testing-library/react'
import { AiStatusBadge } from '../AiStatusBadge'

describe('AiStatusBadge', () => {
  it('renders nothing when idle', () => {
    const { container } = render(<AiStatusBadge status={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows Queued', () => {
    const { container } = render(<AiStatusBadge status="queued" />)
    expect(screen.getByTestId('ai-status')).toHaveTextContent('Queued')
    expect(container.querySelector('.spinner')).toBeNull()
  })

  it('shows AI is writing… with a spinner', () => {
    const { container } = render(<AiStatusBadge status="writing" />)
    expect(screen.getByTestId('ai-status')).toHaveTextContent('AI is writing…')
    expect(container.querySelector('.spinner')).not.toBeNull()
  })

  it.each(['queued', 'writing'] as const)('stalled %s shows Refresh to check, with no spinner or clock', (status) => {
    const { container } = render(<AiStatusBadge status={status} stalled />)
    expect(screen.getByTestId('ai-status')).toHaveTextContent(/^Refresh to check$/)
    expect(container.querySelector('.spinner')).toBeNull()
    expect(container).not.toHaveTextContent('⏱')
  })

  it('stalled renders nothing when idle', () => {
    const { container } = render(<AiStatusBadge status={null} stalled />)
    expect(container).toBeEmptyDOMElement()
  })
})
