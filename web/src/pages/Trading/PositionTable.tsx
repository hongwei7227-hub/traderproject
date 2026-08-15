/**
 * Holdings.
 *
 * Every figure here is derived from filled orders rather than read from a
 * positions table, which is why there is no "as of" timestamp: it is exactly
 * as current as the orders it was computed from.
 */

import { Surface } from '@/components/ui/Surface'
import { cn } from '@/lib/cn'
import type { PositionSummary } from '@/types/api'

const MONEY = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function PositionTable({ positions }: { positions: PositionSummary[] }) {
  const total = positions.reduce((sum, p) => sum + Number(p.cost_basis), 0)

  return (
    <Surface padding="none" className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-ink-faint">
            <th className="px-4 py-2 font-medium">Symbol</th>
            <th className="px-4 py-2 text-right font-medium">Shares</th>
            <th className="px-4 py-2 text-right font-medium">Avg cost</th>
            <th className="px-4 py-2 text-right font-medium">Cost basis</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.symbol} className="border-b border-border last:border-0">
              <td className="px-4 py-3 font-medium text-ink">{position.symbol}</td>
              <td
                className={cn(
                  'px-4 py-3 text-right font-mono',
                  position.quantity < 0 ? 'text-negative' : 'text-ink',
                )}
              >
                {position.quantity}
              </td>
              <td className="px-4 py-3 text-right font-mono text-ink-muted">
                {MONEY.format(Number(position.average_cost))}
              </td>
              <td className="px-4 py-3 text-right font-mono text-ink">
                {MONEY.format(Number(position.cost_basis))}
              </td>
            </tr>
          ))}
        </tbody>
        {positions.length > 1 && (
          <tfoot>
            <tr className="border-t border-border">
              <td className="px-4 py-2 text-xs uppercase tracking-wide text-ink-faint" colSpan={3}>
                Total cost basis
              </td>
              <td className="px-4 py-2 text-right font-mono text-ink">
                {MONEY.format(total)}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </Surface>
  )
}
