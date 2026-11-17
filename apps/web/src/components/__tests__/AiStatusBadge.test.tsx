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
})
