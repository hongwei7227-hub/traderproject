/**
 * Every cache key in one place.
 *
 * Not a style preference. Invalidation works by prefix, so a key written
 * inline at a call site is a key nothing can invalidate as a group — and the
 * failure is silent: the mutation succeeds, the cache keeps the old value, and
 * the interface shows stale data until something else happens to refetch.
 *
 * The shape is hierarchical so that a broad invalidation reaches everything
 * beneath it. `invalidateQueries({ queryKey: keys.threads.all })` clears every
 * thread list and detail; `keys.threads.detail(id)` clears exactly one.
 */

export const keys = {
  threads: {
    all: ['threads'] as const,
    lists: () => [...keys.threads.all, 'list'] as const,
    list: (filters: { limit?: number; offset?: number } = {}) =>
      [...keys.threads.lists(), filters] as const,
    details: () => [...keys.threads.all, 'detail'] as const,
    detail: (threadId: string) => [...keys.threads.details(), threadId] as const,
    transcript: (threadId: string) =>
      [...keys.threads.detail(threadId), 'transcript'] as const,
  },

  workspaces: {
    all: ['workspaces'] as const,
    lists: () => [...keys.workspaces.all, 'list'] as const,
    detail: (workspaceId: string) =>
      [...keys.workspaces.all, 'detail', workspaceId] as const,
  },

  models: {
    all: ['models'] as const,
    catalog: () => [...keys.models.all, 'catalog'] as const,
    preferences: () => [...keys.models.all, 'preferences'] as const,
  },

  account: {
    all: ['account'] as const,
    profile: () => [...keys.account.all, 'profile'] as const,
    usage: () => [...keys.account.all, 'usage'] as const,
    credentials: () => [...keys.account.all, 'credentials'] as const,
  },
} as const

/**
 * Marks a query the interface reads but never fetches.
 *
 * Some state arrives by stream rather than by request — a running turn writes
 * into the cache as events land. Those entries have no fetcher, and without a
 * marker the library's own devtools and any generic refetch helper treat them
 * as broken rather than deliberate.
 */
export const CACHE_ONLY = { cacheOnly: true } as const

export type QueryKey =
  | ReturnType<typeof keys.threads.list>
  | ReturnType<typeof keys.threads.detail>
  | ReturnType<typeof keys.workspaces.detail>
  | ReturnType<typeof keys.models.catalog>
  | ReturnType<typeof keys.account.profile>
