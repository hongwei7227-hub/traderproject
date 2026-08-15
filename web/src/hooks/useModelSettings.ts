/**
 * The catalogue, the per-role assignments, and what the account has spent.
 *
 * Preferences and the catalogue are separate queries deliberately. The
 * catalogue changes when the deployment changes and is worth caching for a
 * long time; the assignments change whenever someone edits them and must be
 * re-read after every mutation. Fetching both together would force one policy
 * on both.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import { del, get, put } from '@/api/client'
import { keys } from '@/lib/queryKeys'
import type {
  ModelList,
  PreferenceList,
  RoleAssignment,
  UsageView,
} from '@/types/api'

export function useModelCatalog(): UseQueryResult<ModelList> {
  return useQuery({
    queryKey: keys.models.catalog(),
    queryFn: () => get<ModelList>('/api/v1/models'),
    // Fixed by the deployment, not by anything a person does here. Refetching
    // it on every visit to the page asks a question whose answer changes at
    // deploy time.
    staleTime: 30 * 60_000,
  })
}

export function usePreferences(): UseQueryResult<PreferenceList> {
  return useQuery({
    queryKey: keys.models.preferences(),
    queryFn: () => get<PreferenceList>('/api/v1/preferences'),
    // Always refetched on mount. The server resolves these through a chain
    // whose other levels — workspace defaults, the platform baseline — can
    // change without this client doing anything.
    staleTime: 0,
  })
}

export function useUsage(): UseQueryResult<UsageView> {
  return useQuery({
    queryKey: keys.account.usage(),
    queryFn: () => get<UsageView>('/api/v1/usage'),
    staleTime: 60_000,
  })
}

export interface PreferenceChange {
  role: string
  /** `null` clears the override and lets the role fall back down the chain. */
  modelId: string | null
}

/**
 * Point a role at a model, or clear it.
 *
 * Not optimistic. The server may refuse — a model that cannot fill the role is
 * rejected there — and showing the new value first would mean showing something
 * that never took, then taking it back. A rejection here is a normal outcome
 * rather than an error, so the honest interface waits.
 *
 * It also cannot predict the answer: clearing a preference falls back to
 * whichever level answers next, which only the server knows.
 */
export function useSetPreference(): UseMutationResult<void, unknown, PreferenceChange> {
  const client = useQueryClient()

  return useMutation({
    // Returns nothing even though the server echoes the assignment back. The
    // echo covers one role, and the refetch below covers all of them, so
    // holding onto it would only invite a call site to render a value that the
    // refetch is about to replace.
    mutationFn: async ({ role, modelId }: PreferenceChange): Promise<void> => {
      if (modelId === null) {
        await del(`/api/v1/preferences/${role}`)
        return
      }
      await put<RoleAssignment>(`/api/v1/preferences/${role}`, { model_id: modelId })
    },

    onSuccess: () => {
      // The whole list, not the one role. Roles share a resolution chain, and
      // a level that answers for one can start answering for another.
      void client.invalidateQueries({ queryKey: keys.models.preferences() })
    },
  })
}
