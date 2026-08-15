/**
 * Orders and positions.
 *
 * Placing an order is not optimistic and does not poll aggressively. The
 * server accepts a proposal for delivery; whether an order exists depends on a
 * worker this process cannot see. Showing a row before that has happened would
 * be inventing one.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError, del, get, post } from '@/api/client'
import { keys } from '@/lib/queryKeys'
import type {
  OrderAccepted,
  OrderList,
  OrderRefusal,
  OrderSummary,
  PlaceOrderBody,
  PositionList,
} from '@/types/api'

/**
 * How often to re-read while something is still in flight.
 *
 * Fills arrive from a broker through a worker, so there is nothing to stream.
 * Three seconds is short enough that a fill does not feel lost and long enough
 * that an idle tab is not making twenty requests a minute.
 */
const WHILE_WORKING = 3_000

export function useOrders(
  options: { workingOnly?: boolean } = {},
): UseQueryResult<OrderList> {
  const { workingOnly = false } = options

  return useQuery({
    queryKey: keys.trading.orders({ workingOnly }),
    queryFn: () =>
      get<OrderList>('/api/v1/orders', { params: { working_only: workingOnly } }),
    // Polls only while something can still change. A list of finished orders
    // is as fresh as it will ever be, and refetching it is pure noise.
    refetchInterval: (query) =>
      (query.state.data?.orders ?? []).some((o) => !o.terminal) ? WHILE_WORKING : false,
    staleTime: 1_000,
  })
}

export function usePositions(): UseQueryResult<PositionList> {
  return useQuery({
    queryKey: keys.trading.positions(),
    queryFn: () => get<PositionList>('/api/v1/positions'),
    staleTime: 10_000,
  })
}

/**
 * Watch a proposal until it becomes an order.
 *
 * The broker id does not exist until the worker has been to the broker, so
 * this is the only handle a client has in between. It stops as soon as the
 * order appears — after that the order list owns the polling.
 */
export function useProposal(proposalId: string | null): UseQueryResult<OrderSummary> {
  return useQuery({
    queryKey: keys.trading.proposal(proposalId ?? ''),
    queryFn: () => get<OrderSummary>(`/api/v1/proposals/${proposalId}`),
    enabled: proposalId !== null,
    // A 404 here means "not yet", not "wrong". Retrying it is the whole point,
    // so the usual give-up-on-4xx rule is deliberately not applied.
    retry: (failureCount, error) =>
      error instanceof ApiError && error.status === 404 && failureCount < 20,
    retryDelay: 1_500,
  })
}

/** The refusal body, when the risk envelope turned an order down. */
export function refusalOf(error: unknown): OrderRefusal | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null
  const detail: unknown = (error as { detail?: unknown }).detail
  if (typeof detail !== 'object' || detail === null) return null

  const record = detail as Record<string, unknown>
  const refusals = record['refusals']
  const lines = record['detail']
  if (!Array.isArray(refusals) || !Array.isArray(lines)) return null

  return {
    refusals: refusals.filter((r): r is string => typeof r === 'string'),
    detail: lines.filter((d): d is string => typeof d === 'string'),
  }
}

export function usePlaceOrder(): UseMutationResult<OrderAccepted, unknown, PlaceOrderBody> {
  const client = useQueryClient()

  return useMutation({
    mutationFn: (body: PlaceOrderBody) => post<OrderAccepted>('/api/v1/orders', body),
    onSuccess: () => {
      // The order will not be there yet. Invalidated anyway so the list starts
      // polling, which is what surfaces it a moment later.
      void client.invalidateQueries({ queryKey: keys.trading.all })
    },
  })
}

export function useCancelOrder(): UseMutationResult<void, unknown, string> {
  const client = useQueryClient()

  return useMutation({
    mutationFn: (brokerOrderId: string) => del(`/api/v1/orders/${brokerOrderId}`),
    onSuccess: () => {
      // Not optimistic: a cancel can lose a race with a fill, and showing the
      // order as cancelled would be asserting an outcome the broker has not
      // agreed to.
      void client.invalidateQueries({ queryKey: keys.trading.all })
    },
  })
}
