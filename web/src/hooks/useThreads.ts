/**
 * Thread queries and mutations.
 *
 * Every cache key comes from the shared factory. A key written inline here
 * would still work for reading and silently fail to be invalidated by anything
 * else — the mutation would succeed and the list would keep showing what it
 * showed before.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import { del, get } from '@/api/client'
import { keys } from '@/lib/queryKeys'
import type { ThreadPage, ThreadSummary } from '@/types/api'

interface ListOptions {
  limit?: number
  offset?: number
}

export function useThreads(options: ListOptions = {}): UseQueryResult<ThreadPage> {
  const { limit = 20, offset = 0 } = options

  return useQuery({
    queryKey: keys.threads.list({ limit, offset }),
    queryFn: () =>
      get<ThreadPage>('/api/v1/threads', { params: { limit, offset } }),
    // A list a minute out of date is harmless; refetching it on every focus
    // is a request per tab switch for no benefit a reader would notice.
    staleTime: 60_000,
  })
}

export function useThread(threadId: string | null): UseQueryResult<ThreadSummary> {
  return useQuery({
    queryKey: keys.threads.detail(threadId ?? ''),
    queryFn: () => get<ThreadSummary>(`/api/v1/threads/${threadId}`),
    enabled: threadId !== null,
  })
}

export function useDeleteThread(): UseMutationResult<void, unknown, string> {
  const client = useQueryClient()

  return useMutation({
    mutationFn: (threadId: string) => del(`/api/v1/threads/${threadId}`),

    onMutate: async (threadId) => {
      // Optimistic, because the alternative is a row that lingers for a round
      // trip after being deleted — which reads as the click not registering.
      await client.cancelQueries({ queryKey: keys.threads.lists() })
      const snapshot = client.getQueriesData<ThreadPage>({
        queryKey: keys.threads.lists(),
      })

      client.setQueriesData<ThreadPage>({ queryKey: keys.threads.lists() }, (page) =>
        page
          ? { ...page, threads: page.threads.filter((t) => t.id !== threadId) }
          : page,
      )

      return { snapshot }
    },

    onError: (_error, _threadId, context) => {
      // Put back exactly what was there. Refetching instead would race with
      // any other mutation in flight.
      for (const [key, data] of context?.snapshot ?? []) {
        client.setQueryData(key, data)
      }
    },

    onSettled: (_data, _error, threadId) => {
      // Fire and forget. The list re-renders when the refetch lands; making
      // the callback wait for it would hold the mutation open for a round trip
      // after the row has already gone.
      void client.invalidateQueries({ queryKey: keys.threads.lists() })
      client.removeQueries({ queryKey: keys.threads.detail(threadId) })
    },
  })
}

/**
 * Mark a thread's cached data stale after a turn finishes.
 *
 * A completed turn changes the thread's title and timestamp server-side.
 * Without this the list keeps the values it fetched before the conversation
 * started.
 */
export function useRefreshAfterTurn(): (threadId: string) => void {
  const client = useQueryClient()

  return (threadId: string) => {
    void client.invalidateQueries({ queryKey: keys.threads.detail(threadId) })
    void client.invalidateQueries({ queryKey: keys.threads.lists() })
  }
}
