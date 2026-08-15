import { AlertTriangle, Info, Scissors } from 'lucide-react'

import { ToolCallCard } from '@/components/chat/ToolCallCard'
import { cn } from '@/lib/cn'
import { wasTruncated, type TurnState } from '@/lib/turn/state'

/**
 * One turn, as the reader sees it.
 *
 * Ordered by what someone is looking for: notices that change how to read the
 * answer, then the answer, then the working that produced it. The reference
 * implementation led with tool activity, which meant the answer arrived below
 * a dozen cards and readers scrolled past it.
 */
export function TurnView({ turn }: { turn: TurnState }) {
  const truncated = wasTruncated(turn)

  return (
    <article className="flex flex-col gap-3">
      {turn.notices.map((notice, index) => (
        <Banner
          key={`${notice.message}-${index}`}
          tone={notice.severity === 'warning' ? 'caution' : 'info'}
        >
          {notice.message}
        </Banner>
      ))}

      {turn.error && (
        <Banner tone="negative">
          {turn.error.message}
          {turn.error.retryable && ' — worth trying again.'}
        </Banner>
      )}

      {turn.text && (
        <div className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink">
          {turn.text}
          {turn.phase === 'thinking' && <Caret />}
        </div>
      )}

      {truncated && (
        <Banner tone="caution" icon={<Scissors className="h-4 w-4" aria-hidden="true" />}>
          {/* A budget-stopped answer reads exactly like a finished one. Saying
              so is the difference between a limit and a defect. */}
          This answer stopped early — it reached a limit rather than finishing.
        </Banner>
      )}

      {turn.toolCalls.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-medium uppercase tracking-wide text-ink-faint">
            {turn.toolCalls.length} step{turn.toolCalls.length === 1 ? '' : 's'}
          </h3>
          {turn.toolCalls.map((call) => (
            <ToolCallCard key={call.callId} call={call} />
          ))}
        </section>
      )}

      {turn.usage && (
        <p className="text-xs text-ink-faint">
          {(turn.usage.input + turn.usage.output).toLocaleString()} tokens ·{' '}
          {turn.usage.model}
        </p>
      )}
    </article>
  )
}

/** A soft pulse marking where text is still arriving. */
function Caret() {
  return (
    <span
      className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-accent"
      aria-hidden="true"
    />
  )
}

type Tone = 'info' | 'caution' | 'negative'

function Banner({
  tone,
  icon,
  children,
}: {
  tone: Tone
  icon?: React.ReactNode
  children: React.ReactNode
}) {
  const tones: Record<Tone, string> = {
    info: 'border-border bg-surface text-ink-muted',
    caution: 'border-caution/40 bg-caution/10 text-ink',
    negative: 'border-negative/40 bg-negative/10 text-ink',
  }

  return (
    <div
      className={cn('flex items-start gap-2 rounded border px-3 py-2 text-sm', tones[tone])}
      // Announced when it appears mid-turn, because a reader watching the
      // answer will not be looking at this part of the page.
      role={tone === 'negative' ? 'alert' : 'status'}
    >
      <span className="mt-0.5 shrink-0">
        {icon ??
          (tone === 'info' ? (
            <Info className="h-4 w-4" aria-hidden="true" />
          ) : (
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          ))}
      </span>
      <span>{children}</span>
    </div>
  )
}
