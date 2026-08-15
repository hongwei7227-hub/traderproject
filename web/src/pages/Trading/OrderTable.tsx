/**
 * Orders, as a table.
 *
 * The fill column shows progress rather than a single number, because a
 * partial fill is the state people most want to see at a glance and "4" next
 * to "10" says less than a bar does.
 */

import { Button } from '@/components/ui/Button'
import { Surface } from '@/components/ui/Surface'
import { useCancelOrder } from '@/hooks/useTrading'
import { cn } from '@/lib/cn'
import type { OrderSummary } from '@/types/api'

/**
 * How a status should read.
 *
 * The server's vocabulary is the order worker's, which is the broker's. It is
 * translated here rather than at the source, because the raw names are the
 * right thing to log and the wrong thing to show.
 */
const STATUS_TONE: Record<string, string> = {
  FILLED: 'text-positive',
  PARTIALLY_FILLED: 'text-caution',
  REJECTED: 'text-negative',
  DENIED: 'text-negative',
  EXPIRED: 'text-ink-faint',
  CANCELED: 'text-ink-faint',
}

export function OrderTable({
  orders,
  cancellable = false,
}: {
  orders: OrderSummary[]
  cancellable?: boolean
}) {
  return (
    <Surface padding="none" className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-ink-faint">
            <Th>Symbol</Th>
            <Th>Side</Th>
            <Th className="text-right">Filled</Th>
            <Th className="text-right">Price</Th>
            <Th>Status</Th>
            {cancellable && <Th />}
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <Row key={order.broker_order_id} order={order} cancellable={cancellable} />
          ))}
        </tbody>
      </table>
    </Surface>
  )
}

function Row({
  order,
  cancellable,
}: {
  order: OrderSummary
  cancellable: boolean
}) {
  const cancel = useCancelOrder()
  const filled = order.quantity > 0 ? order.filled_quantity / order.quantity : 0

  return (
    <tr className="border-b border-border last:border-0">
      <Td>
        <span className="font-medium text-ink">{order.symbol}</span>
        {order.rationale && (
          <p className="mt-0.5 max-w-xs truncate text-xs text-ink-faint" title={order.rationale}>
            {order.rationale}
          </p>
        )}
      </Td>
      <Td>
        <span className={order.side === 'BUY' ? 'text-positive' : 'text-negative'}>
          {order.side}
        </span>
      </Td>
      <Td className="text-right">
        <span className="font-mono text-ink">
          {order.filled_quantity}/{order.quantity}
        </span>
        <div
          className="mt-1 h-1 w-full overflow-hidden rounded bg-border"
          role="progressbar"
          aria-valuenow={Math.round(filled * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${order.symbol} fill progress`}
        >
          <div
            className="h-full bg-accent transition-[width]"
            style={{ width: `${Math.min(100, filled * 100)}%` }}
          />
        </div>
      </Td>
      <Td className="text-right font-mono text-ink-muted">
        {order.filled_quantity > 0 ? order.average_price : (order.limit_price ?? 'MKT')}
      </Td>
      <Td>
        <span className={cn('font-medium', STATUS_TONE[order.status] ?? 'text-ink-muted')}>
          {order.status.replaceAll('_', ' ').toLowerCase()}
        </span>
      </Td>
      {cancellable && (
        <Td className="text-right">
          {/* Only offered while there is something to cancel. The server
              answers 409 for a finished order; not offering the button is the
              same rule expressed where someone can see it. */}
          {!order.terminal && (
            <Button
              size="sm"
              variant="ghost"
              loading={cancel.isPending}
              onClick={() => cancel.mutate(order.broker_order_id)}
            >
              Cancel
            </Button>
          )}
        </Td>
      )}
    </tr>
  )
}

function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <th className={cn('px-4 py-2 font-medium', className)}>{children}</th>
}

function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn('px-4 py-3 align-top', className)}>{children}</td>
}
