import { render, screen } from '@testing-library/react'
import PlansPage from '../page'

describe('PlansPage', () => {
  it('lists the plans, their limits and the coming-soon line', () => {
    render(<PlansPage />)
    expect(screen.getByRole('heading', { name: 'Plans' })).toBeInTheDocument()
    for (const name of ['Free', 'Plus', 'Family']) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
    for (const row of ['Diaries', 'AI drafts per month', 'Photos per entry', 'Manual entries']) {
      expect(screen.getByRole('rowheader', { name: row })).toBeInTheDocument()
    }
    expect(screen.getByText('Paid plans are coming soon.')).toBeInTheDocument()
  })
})
