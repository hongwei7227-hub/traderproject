/**
 * What this account has configured.
 *
 * The page exists mainly so that the resolution chain is visible. A tenant can
 * set a preference through the API without it, but cannot confirm the setting
 * took effect, and cannot tell a role it configured from one running on the
 * platform default. Both are shown here, on every row.
 */

import { useModelCatalog, usePreferences, useUsage } from '@/hooks/useModelSettings'
import { RoleRow } from '@/pages/Settings/RoleRow'
import { UsageSummary } from '@/pages/Settings/UsageSummary'
import { Surface } from '@/components/ui/Surface'

export function SettingsPage() {
  const catalog = useModelCatalog()
  const preferences = usePreferences()
  const usage = useUsage()

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <header className="mb-8">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Settings</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Model assignments and usage for this account.
          </p>
        </header>

        <section aria-labelledby="models-heading" className="mb-8">
          <h2 id="models-heading" className="mb-1 text-sm font-medium text-ink">
            Models
          </h2>
          <p className="mb-4 text-sm text-ink-muted">
            A single turn uses several models. Assigning cheaper ones to the
            supporting roles is the difference between a turn that costs cents
            and one that costs dollars.
          </p>

          {preferences.isPending || catalog.isPending ? (
            <Skeleton rows={4} />
          ) : preferences.isError || catalog.isError ? (
            <Failed
              onRetry={() => {
                void preferences.refetch()
                void catalog.refetch()
              }}
            />
          ) : (
            <Surface padding="none" className="divide-y divide-border">
              {preferences.data.roles.map((assignment) => (
                <RoleRow
                  key={assignment.role}
                  assignment={assignment}
                  // Only models that can fill the role. The server refuses the
                  // rest, and offering a choice that will be rejected is a
                  // worse way to tell someone than not offering it.
                  models={catalog.data.models.filter((model) =>
                    model.eligible_roles.includes(assignment.role),
                  )}
                />
              ))}
            </Surface>
          )}
        </section>

        <section aria-labelledby="usage-heading">
          <h2 id="usage-heading" className="mb-1 text-sm font-medium text-ink">
            Usage
          </h2>
          <p className="mb-4 text-sm text-ink-muted">
            Tokens consumed by this account, counted from the record of each
            turn.
          </p>

          {usage.isPending ? (
            <Skeleton rows={1} />
          ) : usage.isError ? (
            <Failed onRetry={() => void usage.refetch()} />
          ) : (
            <UsageSummary usage={usage.data} />
          )}
        </section>
      </div>
    </div>
  )
}

function Skeleton({ rows }: { rows: number }) {
  return (
    <Surface padding="none" className="divide-y divide-border" aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-[72px] animate-pulse bg-canvas/50" />
      ))}
      <span className="sr-only">Loading</span>
    </Surface>
  )
}

function Failed({ onRetry }: { onRetry: () => void }) {
  return (
    <Surface className="text-sm text-ink-muted">
      <p>Could not load this section.</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 text-accent underline underline-offset-2"
      >
        Try again
      </button>
    </Surface>
  )
}
