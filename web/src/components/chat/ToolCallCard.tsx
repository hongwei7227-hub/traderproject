import { Check, ChevronRight, Loader2, X } from 'lucide-react'
import { useState } from 'react'

import { cn } from '@/lib/cn'
import type { ToolCall } from '@/lib/turn/state'

/**
 * One tool call, collapsed by default.
 *
 * A turn may make a dozen; expanded by default they bury the answer they exist
 * to support. Collapsed, they read as a list of what was done — which is what
 * someone scanning the reply actually wants.
 *
 * A failed call is the exception and opens itself, because a failure is the
 * one case where the detail explains something about the answer above it.
 */
export function ToolCallCard({ call }: { call: ToolCall }) {
  const failed = call.result?.ok === false
  const [open, setOpen] = useState(failed)
  const running = call.result === undefined

  return (
    <div
      className={cn(
        'rounded border text-sm transition-colors',
        failed ? 'border-negative/40 bg-negative/5' : 'border-border bg-surface',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <StatusIcon running={running} failed={failed} />

        <span className="font-mono text-xs text-ink">{call.name}</span>

        <span className="flex-1 truncate text-xs text-ink-muted">
          {call.result?.summary ?? 'running…'}
        </span>

        <ChevronRight
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-ink-faint transition-transform',
            open && 'rotate-90',
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border px-3 py-2">
          <Arguments values={call.arguments} />
          {call.result && (
            <p className="mt-2 whitespace-pre-wrap text-xs text-ink-muted">
              {call.result.summary}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function StatusIcon({ running, failed }: { running: boolean; failed: boolean }) {
  if (running) {
    return (
      <Loader2
        className="h-3.5 w-3.5 shrink-0 animate-spin text-ink-faint"
        aria-label="Running"
      />
    )
  }
  if (failed) {
    return <X className="h-3.5 w-3.5 shrink-0 text-negative" aria-label="Failed" />
  }
  return <Check className="h-3.5 w-3.5 shrink-0 text-positive" aria-label="Finished" />
}

function Arguments({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values)
  if (entries.length === 0) {
    return <p className="text-xs text-ink-faint">No arguments</p>
  }

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-ink-faint">{key}</dt>
          {/* Truncated rather than wrapped: an argument can be an entire
              document, and a card that grows to fit one stops being scannable. */}
          <dd className="truncate font-mono text-ink-muted">{format(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function format(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
