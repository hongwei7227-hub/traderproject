/**
 * The form that proposes a trade.
 *
 * Its real job is showing a refusal well. The server checks the risk envelope
 * and answers with every limit that was breached and by how much — reporting
 * only the first would turn correcting the order into a guessing game where
 * each fix reveals the next objection. So all of them are rendered.
 */

import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/Button'
import { Surface } from '@/components/ui/Surface'
import { AnalystCard } from '@/pages/Trading/AnalystCard'
import { refusalOf, usePlaceOrder } from '@/hooks/useTrading'
import { cn } from '@/lib/cn'
import type { OrderRefusal, OrderSide } from '@/types/api'

type OrderType = 'limit' | 'market'

/**
 * What each refusal means, in a sentence someone can act on.
 *
 * The server sends both a machine-readable code and its own prose. The code is
 * used for the heading because it is stable; the prose carries the numbers,
 * which is the part that says how far over the line the order was.
 */
const REFUSAL_TITLES: Record<string, string> = {
  not_in_universe: 'Symbol not tradable',
  order_too_large: 'Order too large',
  position_too_large: 'Position would be too large',
  daily_limit_reached: 'Daily order limit reached',
  no_equity: 'No equity to size against',
  selling_what_is_not_held: 'Selling more than is held',
}

export function OrderTicket() {
  const place = usePlaceOrder()

  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState<OrderSide>('BUY')
  const [orderType, setOrderType] = useState<OrderType>('limit')
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [rationale, setRationale] = useState('')
  const [accepted, setAccepted] = useState<string | null>(null)

  const refusal: OrderRefusal | null = refusalOf(place.error)
  const otherError = place.isError && refusal === null

  function submit(event: FormEvent) {
    event.preventDefault()
    setAccepted(null)

    place.mutate(
      {
        symbol: symbol.trim().toUpperCase(),
        side,
        quantity: Number(quantity),
        // Exactly one of the two, never both: a limit order already carries its
        // reference, and sending a second number invites the two disagreeing.
        limit_price: orderType === 'limit' ? price : null,
        reference_price: orderType === 'market' ? price : null,
        rationale: rationale.trim(),
      },
      {
        onSuccess: (result) => {
          setAccepted(result.proposal_id)
          setQuantity('')
          setRationale('')
        },
      },
    )
  }

  const ready = symbol.trim().length > 0 && Number(quantity) > 0 && Number(price) > 0

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_18rem]">
      <Surface>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field id="symbol" label="Symbol">
              <input
                id="symbol"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="NVDA"
                className={inputClass}
                autoComplete="off"
              />
            </Field>

            <Field id="quantity" label="Shares">
              <input
                id="quantity"
                type="number"
                min={1}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className={inputClass}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Choice
              label="Side"
              value={side}
              options={[
                { value: 'BUY', label: 'Buy' },
                { value: 'SELL', label: 'Sell' },
              ]}
              onChange={setSide}
            />
            <Choice
              label="Type"
              value={orderType}
              options={[
                { value: 'limit', label: 'Limit' },
                { value: 'market', label: 'Market' },
              ]}
              onChange={setOrderType}
            />
          </div>

          <Field
            id="price"
            label={orderType === 'limit' ? 'Limit price' : 'Reference price'}
            hint={
              orderType === 'market'
                ? 'A market order still needs a price to be sized against a limit.'
                : undefined
            }
          >
            <input
              id="price"
              type="number"
              step="0.01"
              min={0}
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className={inputClass}
            />
          </Field>

          <Field
            id="rationale"
            label="Rationale"
            hint="Stored with the order. A conversation can be compacted away; this outlives it."
          >
            <textarea
              id="rationale"
              rows={2}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              className={cn(inputClass, 'h-auto py-2')}
            />
          </Field>

          {refusal !== null && <Refused refusal={refusal} />}

          {otherError && (
            <p role="alert" className="text-sm text-negative">
              The order could not be submitted.
            </p>
          )}

          {accepted !== null && (
            <p role="status" className="text-sm text-positive">
              Queued. The broker sees it once the worker picks it up.
            </p>
          )}

          <Button
            type="submit"
            variant="primary"
            loading={place.isPending}
            disabled={!ready}
          >
            {side === 'BUY' ? 'Buy' : 'Sell'} {symbol.trim().toUpperCase() || '—'}
          </Button>
        </form>
      </Surface>

      <AnalystCard symbol={symbol.trim().toUpperCase()} price={Number(price) || undefined} />
    </div>
  )
}

function Refused({ refusal }: { refusal: OrderRefusal }) {
  return (
    <div role="alert" className="rounded border border-negative/40 bg-negative/5 p-3">
      <p className="text-sm font-medium text-negative">
        {refusal.refusals.length > 1 ? 'This order breaches several limits' : 'Refused'}
      </p>
      <ul className="mt-2 space-y-1">
        {refusal.refusals.map((code, index) => (
          <li key={code} className="text-sm text-ink-muted">
            <span className="text-ink">{REFUSAL_TITLES[code] ?? code}</span>
            {refusal.detail[index] && <> — {refusal.detail[index]}</>}
          </li>
        ))}
      </ul>
    </div>
  )
}

const inputClass = cn(
  'h-9 w-full rounded border border-border bg-surface px-2 text-sm text-ink',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
)

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string
  label: string
  // `| undefined` rather than just optional: the project builds with
  // exactOptionalPropertyTypes, which distinguishes "absent" from "present and
  // undefined", and a conditional hint produces the second.
  hint?: string | undefined
  children: React.ReactNode
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-ink-faint">{hint}</p>}
    </div>
  )
}

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: ReadonlyArray<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <div>
      <span className="block text-sm font-medium text-ink">{label}</span>
      <div
        role="radiogroup"
        aria-label={label}
        className="mt-1 flex gap-1 rounded border border-border p-1"
      >
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex-1 rounded px-2 py-1 text-sm transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              value === option.value
                ? 'bg-raised text-ink'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
