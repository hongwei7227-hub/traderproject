/**
 * One role, its model, and where that choice came from.
 *
 * The provenance line is the part worth having. Without it a row showing
 * "flagship" is ambiguous between a deliberate choice and a default that
 * happens to look like one, and the two behave differently when the deployment
 * changes underneath.
 */

import { useState } from 'react'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { useSetPreference } from '@/hooks/useModelSettings'
import { cn } from '@/lib/cn'
import type { ModelView, RoleAssignment } from '@/types/api'

/**
 * What each role does, in the terms a person choosing a model needs.
 *
 * Roles the server adds later fall through to their identifier rather than
 * disappearing: an unexplained row is worse than a plain one, but both beat a
 * row that is silently missing.
 */
const ROLE_COPY: Record<string, { title: string; blurb: string }> = {
  primary: {
    title: 'Primary',
    blurb: 'Drives the conversation and decides which tools to call.',
  },
  swift: {
    title: 'Swift',
    blurb: 'Short, latency-sensitive replies where judgment matters less.',
  },
  condense: {
    title: 'Condense',
    blurb: 'Summarises history once a conversation outgrows the context window.',
  },
  extract: {
    title: 'Extract',
    blurb: 'Pulls the readable text out of fetched pages and documents.',
  },
}

const PROVENANCE: Record<string, string> = {
  'explicit-request': 'chosen for this request',
  'tenant-preference': 'your choice',
  'workspace-default': 'workspace default',
  'system-baseline': 'platform default',
}

export function RoleRow({
  assignment,
  models,
}: {
  assignment: RoleAssignment
  models: ModelView[]
}) {
  const setPreference = useSetPreference()
  const [rejected, setRejected] = useState<string | null>(null)

  const copy = ROLE_COPY[assignment.role]
  const selectId = `role-${assignment.role}`

  // The stored value may name a model the deployment has since removed. Left
  // in the list rather than snapping the select to something else, because
  // silently changing the displayed choice is how a person comes to believe
  // they configured something they did not.
  const missing =
    models.every((model) => model.id !== assignment.model_id) &&
    assignment.overridden

  function change(modelId: string | null) {
    setRejected(null)
    setPreference.mutate(
      { role: assignment.role, modelId },
      {
        onError: (error) =>
          setRejected(
            error instanceof ApiError ? error.message : 'Could not save that.',
          ),
      },
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-4 p-4">
      <div className="min-w-0 flex-1">
        <label htmlFor={selectId} className="block text-sm font-medium text-ink">
          {copy?.title ?? assignment.role}
        </label>
        <p className="mt-0.5 text-sm text-ink-muted">{copy?.blurb}</p>
        <p className="mt-1 text-xs text-ink-faint">
          {PROVENANCE[assignment.decided_by] ?? assignment.decided_by}
          {assignment.requires.length > 0 && (
            <> · needs {assignment.requires.join(', ').replace(/_/g, ' ')}</>
          )}
        </p>
        {rejected !== null && (
          <p role="alert" className="mt-1 text-xs text-negative">
            {rejected}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <select
          id={selectId}
          value={assignment.model_id}
          disabled={setPreference.isPending}
          onChange={(event) => change(event.target.value)}
          className={cn(
            'h-9 rounded border border-border bg-surface px-2 text-sm text-ink',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
            'disabled:opacity-50',
          )}
        >
          {missing && (
            <option value={assignment.model_id}>
              {assignment.model_id} (unavailable)
            </option>
          )}
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.id} — {model.provider_name}
            </option>
          ))}
        </select>

        <Button
          variant="ghost"
          size="sm"
          // Hidden rather than disabled when there is nothing to reset: a
          // permanently greyed control on every row reads as broken.
          className={cn(!assignment.overridden && 'invisible')}
          aria-hidden={!assignment.overridden}
          tabIndex={assignment.overridden ? undefined : -1}
          loading={setPreference.isPending}
          onClick={() => change(null)}
        >
          Reset
        </Button>
      </div>
    </div>
  )
}
