import AppHeader from '@/components/AppHeader'

const PLANS = ['Free', 'Plus', 'Family']
const ROWS: { label: string; values: string[] }[] = [
  { label: 'Diaries', values: ['1', '2', '4'] },
  { label: 'AI drafts per month', values: ['5', 'Unlimited', 'Unlimited'] },
  { label: 'Photos per entry', values: ['1', 'Unlimited', 'Unlimited'] },
  { label: 'Manual entries', values: ['Unlimited', 'Unlimited', 'Unlimited'] },
  { label: 'Backfill', values: ['—', '✓', '✓'] },
]

export default function PlansPage() {
  return (
    <>
      <AppHeader back={{ href: '/diaries', label: '← Diaries' }} />

      <div className="page-container">
        <div className="page-header">
          <h1 className="page-title">Plans</h1>
        </div>

        <table className="plans-table">
          <thead>
            <tr>
              <td />
              {PLANS.map(p => (
                <th key={p} scope="col">{p}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map(r => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                {r.values.map((v, i) => (
                  <td key={PLANS[i]}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        <p className="limit-note">Paid plans are coming soon.</p>
      </div>
    </>
  )
}
