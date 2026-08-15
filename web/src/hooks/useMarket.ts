/**
 * Analyst coverage and billing.
 *
 * Both sit in front of services that can be down independently of this
 * platform, which the server reports as a 503. That is worth distinguishing
 * from an ordinary error: the page should show a gap where a card would be and
 * keep working, not present itself as broken.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError, get, post } from '@/api/client'
import { keys } from '@/lib/queryKeys'
import type {
  AnalystRating,
  MembershipStatus,
  PlanList,
  RechargeOrderStatus,
} from '@/types/api'

/** Whether a failure was the upstream service rather than this platform. */
export function isDependencyDown(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503
}

/** Whether the symbol simply has no coverage, which is not a failure. */
export function isUncovered(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

export function useAnalystRating(
  symbol: string | null,
  price?: number,
): UseQueryResult<AnalystRating> {
  return useQuery({
    queryKey: keys.analyst.rating(symbol ?? ''),
    queryFn: () =>
      get<AnalystRating>(`/api/v1/stocks/${symbol}/analyst`, {
        params: price ? { price } : undefined,
      }),
    enabled: symbol !== null && symbol.length > 0,
    // Ratings change on the timescale of an analyst changing their mind.
    staleTime: 15 * 60_000,
    // A symbol with no coverage stays uncovered; retrying asks the same
    // question four more times for the same answer.
    retry: (failureCount, error) =>
      !isUncovered(error) && !isDependencyDown(error) && failureCount < 2,
  })
}

export function usePlans(): UseQueryResult<PlanList> {
  return useQuery({
    queryKey: keys.billing.plans(),
    queryFn: () => get<PlanList>('/api/v1/billing/plans'),
    staleTime: 60 * 60_000,
  })
}

export function useMembership(): UseQueryResult<MembershipStatus> {
  return useQuery({
    queryKey: keys.billing.membership(),
    queryFn: () => get<MembershipStatus>('/api/v1/billing/membership'),
    staleTime: 60_000,
  })
}

export interface RechargeRequest {
  planId: number
  /**
   * Idempotency key, minted by the caller before the first attempt.
   *
   * A double-clicked buy must produce one order, which is only possible if
   * both requests carry the same key — so it cannot be generated inside the
   * mutation, which runs once per click.
   */
  requestId: string
}

export function useStartRecharge(): UseMutationResult<
  RechargeOrderStatus,
  unknown,
  RechargeRequest
> {
  const client = useQueryClient()

  return useMutation({
    mutationFn: ({ planId, requestId }: RechargeRequest) =>
      post<RechargeOrderStatus>('/api/v1/billing/recharge', {
        plan_id: planId,
        request_id: requestId,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.billing.membership() })
    },
  })
}

/**
 * Follow a top-up until it settles.
 *
 * Payment happens somewhere this client cannot see — a gateway, a callback to
 * the recharge service — so the only way to learn the outcome is to ask.
 * Stops once the order is no longer awaiting payment.
 */
export function useRechargeStatus(
  orderId: number | null,
): UseQueryResult<RechargeOrderStatus> {
  return useQuery({
    queryKey: keys.billing.recharge(orderId ?? 0),
    queryFn: () => get<RechargeOrderStatus>(`/api/v1/billing/recharge/${orderId}`),
    enabled: orderId !== null,
    refetchInterval: (query) => (query.state.data?.awaiting_payment ? 2_000 : false),
  })
}
