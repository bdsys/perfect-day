import { render, screen } from '@testing-library/react'
import PlansPage from '../page'

describe('PlansPage', () => {
  it('lists the plans, their limits and the coming-soon line', () => {
    render(<PlansPage />)
    expect(screen.getByRole('heading', { name: 'Plans' })).toBeInTheDocument()
    for (const name of ['Free', 'Plus', 'Family']) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
    for (const row of ['Diaries', 'AI drafts per month', 'Photos per entry', 'Manual entries', 'Backfill']) {
      expect(screen.getByRole('rowheader', { name: row })).toBeInTheDocument()
    }
    const backfill = screen.getByRole('rowheader', { name: 'Backfill' }).closest('tr')!
    expect(Array.from(backfill.querySelectorAll('td')).map(td => td.textContent)).toEqual(['—', '✓', '✓'])
    const values = (name: string) =>
      Array.from(screen.getByRole('rowheader', { name }).closest('tr')!.querySelectorAll('td')).map(td => td.textContent)
    expect(values('People who can read')).toEqual(['3', '10 per diary', 'Unlimited'])
    expect(values('Editors')).toEqual(['—', '—', '✓'])
    expect(screen.getByText('Paid plans are coming soon.')).toBeInTheDocument()
  })
})
