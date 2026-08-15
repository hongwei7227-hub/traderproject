/**
 * Membership and top-ups.
 *
 * Payment happens somewhere this client cannot see — the recharge service
 * hands the order to a gateway and hears back later. So buying does not
 * pretend to complete: it creates an order and then follows it until it
 * settles, which is the only honest thing a client can do about a payment it
 * is not part of.
 */

import { useState } from 'react'

import { Button } from '@/components/ui/Button'
import { Surface } from '@/components/ui/Surface'
import {
  isDependencyDown,
  useMembership,
  usePlans,
  useRechargeStatus,
  useStartRecharge,
} from '@/hooks/useMarket'
import { cn } from '@/lib/cn'
import type { BillingPlan } from '@/types/api'

export function BillingPage() {
  const plans = usePlans()
  const membership = useMembership()
  const start = useStartRecharge()
  const [watching, setWatching] = useState<number | null>(null)
  const order = useRechargeStatus(watching)

  /**
   * One key per attempt, minted before the request.
   *
   * A double-clicked buy must produce one order, which is only possible if both
   * requests carry the same key — so it cannot be generated inside the
   * mutation, which runs once per click.
   */
  const [requestKeys] = useState(() => new Map<number, string>())
  function keyFor(planId: number): string {
    const existing = requestKeys.get(planId)
    if (existing) return existing
    const minted = `${planId}-${crypto.randomUUID()}`
    requestKeys.set(planId, minted)
    return minted
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <header className="mb-8">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Membership</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Your plan, and what it costs to change it.
          </p>
        </header>

        <section aria-labelledby="current-heading" className="mb-8">
          <h2 id="current-heading" className="mb-3 text-sm font-medium text-ink">
            Current
          </h2>
          {membership.isPending ? (
            <Surface className="text-sm text-ink-muted">Loading</Surface>
          ) : isDependencyDown(membership.error) ? (
            <Surface className="text-sm text-ink-muted">
              Billing is unavailable right now. Everything else keeps working.
            </Surface>
          ) : membership.isError ? (
            <Surface className="text-sm text-ink-muted">
              Could not read your membership.
            </Surface>
          ) : (
            <Surface>
              <p className="text-lg font-semibold text-ink">
                {membership.data.level > 0 ? `Level ${membership.data.level}` : 'Free'}
              </p>
              {/* Level alone is not enough — an expired membership still has
                  one, and showing it without the expiry reads as active. */}
              {membership.data.expires_at && (
                <p
                  className={cn(
                    'mt-1 text-sm',
                    membership.data.active ? 'text-ink-muted' : 'text-negative',
                  )}
                >
                  {membership.data.active ? 'Renews' : 'Expired'}{' '}
                  {new Date(membership.data.expires_at).toLocaleDateString()}
                </p>
              )}
            </Surface>
          )}
        </section>

        <section aria-labelledby="plans-heading">
          <h2 id="plans-heading" className="mb-3 text-sm font-medium text-ink">
            Plans
          </h2>

          {plans.isPending ? (
            <Surface className="text-sm text-ink-muted">Loading</Surface>
          ) : isDependencyDown(plans.error) ? (
            <Surface className="text-sm text-ink-muted">
              Plans are unavailable right now.
            </Surface>
          ) : plans.isError || plans.data.plans.length === 0 ? (
            <Surface className="text-sm text-ink-muted">No plans on offer.</Surface>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {plans.data.plans.map((plan) => (
                <PlanCard
                  key={plan.id}
                  plan={plan}
                  busy={start.isPending}
                  onBuy={() =>
                    start.mutate(
                      { planId: plan.id, requestId: keyFor(plan.id) },
                      { onSuccess: (created) => setWatching(created.id) },
                    )
                  }
                />
              ))}
            </div>
          )}

          {start.isError && (
            <p role="alert" className="mt-3 text-sm text-negative">
              That purchase could not be started.
            </p>
          )}

          {order.data && (
            <Surface className="mt-4">
              <p className="text-sm text-ink">
                Order #{order.data.id} — {order.data.state}
              </p>
              <p className="mt-1 text-sm text-ink-muted">
                {order.data.awaiting_payment
                  ? 'Waiting for the payment to clear. This page updates itself.'
                  : 'Settled.'}
              </p>
            </Surface>
          )}
        </section>
      </div>
    </div>
  )
}

function PlanCard({
  plan,
  busy,
  onBuy,
}: {
  plan: BillingPlan
  busy: boolean
  onBuy: () => void
}) {
  return (
    <Surface className="flex flex-col gap-3">
      <div>
        <p className="font-medium text-ink">{plan.name}</p>
        <p className="mt-1 font-mono text-2xl text-ink">{plan.price}</p>
        {/* A yearly plan and a monthly one are not comparable by the headline
            price, which is the number a reader sees first. */}
        <p className="text-xs text-ink-faint">
          {plan.monthly_price} / month · {plan.duration_days} days
        </p>
      </div>
      <Button variant="primary" onClick={onBuy} loading={busy} className="mt-auto">
        Choose
      </Button>
    </Surface>
  )
}
