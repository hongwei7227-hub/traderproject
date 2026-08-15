/**
 * What the street thinks, beside the ticket.
 *
 * The analyst service can be down independently of everything else. When it
 * is, this says so and the rest of the page keeps working — which is the whole
 * reason the server distinguishes a 503 from an error.
 */

import { Surface } from '@/components/ui/Surface'
import { isDependencyDown, isUncovered, useAnalystRating } from '@/hooks/useMarket'
import { cn } from '@/lib/cn'

const PERCENT = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 1,
})

export function AnalystCard({
  symbol,
  price,
}: {
  symbol: string
  price?: number | undefined
}) {
  const rating = useAnalystRating(symbol.length >= 1 ? symbol : null, price)

  if (!symbol) {
    return <Frame muted>Enter a symbol to see analyst coverage.</Frame>
  }

  if (rating.isPending) {
    return <Frame muted>Looking up {symbol}…</Frame>
  }

  if (isUncovered(rating.error)) {
    // Not a failure. Most listed companies have no coverage worth the name.
    return <Frame muted>No analyst coverage for {symbol}.</Frame>
  }

  if (isDependencyDown(rating.error)) {
    return (
      <Frame muted>
        Analyst data is unavailable right now. Orders are unaffected.
      </Frame>
    )
  }

  if (rating.isError || !rating.data) {
    return <Frame muted>Could not load coverage for {symbol}.</Frame>
  }

  const data = rating.data

  return (
    <Surface className="space-y-3">
      <div>
        <p className="text-sm font-medium text-ink">{data.symbol}</p>
        {data.company_name && (
          <p className="text-xs text-ink-faint">{data.company_name}</p>
        )}
      </div>

      <div className="flex items-baseline justify-between">
        <span className="text-lg font-semibold text-ink">{data.consensus || '—'}</span>
        <span className="text-xs text-ink-faint">
          {data.analyst_count} {data.analyst_count === 1 ? 'analyst' : 'analysts'}
        </span>
      </div>

      {data.target_price !== null && (
        <div>
          <p className="text-xs uppercase tracking-wide text-ink-faint">Target</p>
          <p className="font-mono text-ink">{data.target_price.toFixed(2)}</p>
          {data.target_low !== null && data.target_high !== null && (
            <p className="text-xs text-ink-faint">
              {data.target_low.toFixed(2)} – {data.target_high.toFixed(2)}
            </p>
          )}
          {/* Null when there is no target or no price. Rendering 0% would
              assert the stock is fairly valued, which is a different statement
              from having nothing to say. */}
          {data.upside !== null && (
            <p
              className={cn(
                'mt-1 text-sm font-medium',
                data.upside >= 0 ? 'text-positive' : 'text-negative',
              )}
            >
              {PERCENT.format(data.upside)} to target
            </p>
          )}
        </div>
      )}

      {data.recent_grades.length > 0 && (
        <div>
          <p className="text-xs uppercase tracking-wide text-ink-faint">Recent</p>
          <ul className="mt-1 space-y-1">
            {data.recent_grades.slice(0, 4).map((grade, index) => (
              <li key={`${grade.firm}-${index}`} className="text-xs text-ink-muted">
                <span className="text-ink">{grade.firm}</span>{' '}
                <span
                  className={cn(
                    grade.action === 'upgrade' && 'text-positive',
                    grade.action === 'downgrade' && 'text-negative',
                  )}
                >
                  {grade.from_grade} → {grade.to_grade}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Surface>
  )
}

function Frame({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <Surface>
      <p className={muted ? 'text-sm text-ink-muted' : 'text-sm text-ink'}>{children}</p>
    </Surface>
  )
}
