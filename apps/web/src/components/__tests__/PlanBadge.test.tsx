import { render, screen, fireEvent } from '@testing-library/react'
import PlanBadge from '../PlanBadge'
import LimitNote from '../LimitNote'

describe('PlanBadge', () => {
  it('shows what is left, the reset date, colour and an upgrade link', () => {
    render(<PlanBadge label="AI drafts" used={2} limit={5} resetsOn="2026-12-01" />)
    const badge = screen.getByRole('button', { name: 'AI drafts: 3 of 5 left · resets Dec 1' })
    expect(badge).toHaveAttribute('data-color', 'yellow')
    expect(screen.getByRole('link', { name: 'Upgrade now' })).toHaveAttribute('href', '/plans')
  })

  it('omits the reset text without resetsOn', () => {
    render(<PlanBadge label="Diaries" used={0} limit={1} />)
    expect(screen.getByRole('button')).toHaveTextContent(/^Diaries: 1 of 1 left$/)
  })

  it('never shows a negative number', () => {
    render(<PlanBadge label="AI drafts" used={7} limit={5} />)
    const badge = screen.getByRole('button')
    expect(badge).toHaveTextContent('0 of 5 left')
    expect(badge).toHaveAttribute('data-color', 'red')
  })

  it('toggles the details on click', () => {
    render(
      <PlanBadge label="AI drafts" used={1} limit={5}
        details={[{ label: 'Plan', text: 'Free plan' }]} />,
    )
    const badge = screen.getByRole('button')
    expect(badge).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Free plan')).not.toBeInTheDocument()
    fireEvent.click(badge)
    expect(badge).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Free plan')).toBeInTheDocument()
    fireEvent.click(badge)
    expect(screen.queryByText('Free plan')).not.toBeInTheDocument()
  })

  it('links the button to the details list it opens', () => {
    render(
      <PlanBadge label="AI drafts" used={1} limit={5}
        details={[{ label: 'Plan', text: 'Free plan' }]} />,
    )
    const badge = screen.getByRole('button')
    const listId = badge.getAttribute('aria-controls')
    expect(listId).toBeTruthy()
    fireEvent.click(badge)
    expect(screen.getByRole('list')).toHaveAttribute('id', listId)
  })

  it('without details it is not announced as expandable', () => {
    render(<PlanBadge label="Diaries" used={0} limit={1} />)
    const badge = screen.getByRole('button')
    expect(badge).not.toHaveAttribute('aria-expanded')
    expect(badge).not.toHaveAttribute('aria-controls')
  })
})

describe('LimitNote', () => {
  it('renders the text with the id a control can point at', () => {
    render(<LimitNote id="n1">Free plan: 1 photo per entry.</LimitNote>)
    expect(screen.getByText('Free plan: 1 photo per entry.')).toHaveAttribute('id', 'n1')
  })
})
