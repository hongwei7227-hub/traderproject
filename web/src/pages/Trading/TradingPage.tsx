/**
 * Orders and holdings.
 *
 * Three regions: what is held, what is in flight, and the form that adds to
 * both. The form is first on the page because it is what someone came to do,
 * and the lists below it are the consequence.
 */

import { Surface } from '@/components/ui/Surface'
import { OrderTable } from '@/pages/Trading/OrderTable'
import { OrderTicket } from '@/pages/Trading/OrderTicket'
import { PositionTable } from '@/pages/Trading/PositionTable'
import { useOrders, usePositions } from '@/hooks/useTrading'

export function TradingPage() {
  const orders = useOrders()
  const positions = usePositions()

  const working = (orders.data?.orders ?? []).filter((o) => !o.terminal)

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-8">
        <header className="mb-8">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Trading</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Orders are proposed here and executed by the order worker. A
            proposal that passes the risk envelope is queued; the broker sees it
            a moment later.
          </p>
        </header>

        <section aria-labelledby="ticket-heading" className="mb-8">
          <h2 id="ticket-heading" className="mb-3 text-sm font-medium text-ink">
            New order
          </h2>
          <OrderTicket />
        </section>

        <section aria-labelledby="working-heading" className="mb-8">
          <h2 id="working-heading" className="mb-3 text-sm font-medium text-ink">
            In flight
            {working.length > 0 && (
              <span className="ml-2 text-ink-faint">{working.length}</span>
            )}
          </h2>
          {orders.isPending ? (
            <Placeholder>Loading</Placeholder>
          ) : orders.isError ? (
            <Placeholder>Could not load orders.</Placeholder>
          ) : working.length === 0 ? (
            <Placeholder>Nothing working.</Placeholder>
          ) : (
            <OrderTable orders={working} cancellable />
          )}
        </section>

        <section aria-labelledby="positions-heading" className="mb-8">
          <h2 id="positions-heading" className="mb-3 text-sm font-medium text-ink">
            Positions
          </h2>
          {positions.isPending ? (
            <Placeholder>Loading</Placeholder>
          ) : positions.isError ? (
            <Placeholder>Could not load positions.</Placeholder>
          ) : positions.data.positions.length === 0 ? (
            <Placeholder>No open positions.</Placeholder>
          ) : (
            <PositionTable positions={positions.data.positions} />
          )}
        </section>

        <section aria-labelledby="history-heading">
          <h2 id="history-heading" className="mb-3 text-sm font-medium text-ink">
            History
          </h2>
          {orders.data && orders.data.orders.length > 0 ? (
            <OrderTable orders={orders.data.orders} />
          ) : (
            <Placeholder>No orders yet.</Placeholder>
          )}
        </section>
      </div>
    </div>
  )
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <Surface className="text-sm text-ink-muted">
      <p>{children}</p>
    </Surface>
  )
}
