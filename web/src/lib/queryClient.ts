/**
 * Cache policy.
 *
 * The defaults here are chosen against one question: what does the reader see
 * if this value is stale? A thread list that is a minute out of date is
 * harmless; a running turn's state that is a second out of date is wrong.
 */

import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'

/** Nothing is gained by retrying a request the server refused on its merits. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && !error.retryable) return false
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Long enough that navigating between pages does not refetch
        // everything, short enough that returning to a tab shows current data.
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: shouldRetry,
        // Refetching on focus is right for lists and wrong for a conversation
        // mid-stream, so it is opted into per query rather than defaulted on.
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
      },
      mutations: {
        // A mutation that failed may have applied. Retrying one without an
        // idempotency key risks doing it twice, which for a message send means
        // the tenant pays twice.
        retry: false,
      },
    },
  })
}

/**
 * Clear everything belonging to the previous account.
 *
 * Must run on sign-out. A cache that survives it shows one person another's
 * threads for as long as the entries stay fresh — and because the keys are
 * identical between accounts, nothing about the stale entry looks wrong.
 */
export function resetForSignOut(client: QueryClient): void {
  // Not awaited, and deliberately so: the cache must be gone before the next
  // render regardless of whether in-flight requests have finished unwinding.
  // `void` marks the omission as a decision rather than a missing `await`.
  void client.cancelQueries()
  client.clear()
}
