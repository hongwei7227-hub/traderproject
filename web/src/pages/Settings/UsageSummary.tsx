/**
 * Tokens consumed, split the way they are priced.
 *
 * Input and output stay separate because a single total hides which way an
 * account leans — and they cost different amounts, so the total alone does not
 * predict the bill.
 */

import { Surface } from '@/components/ui/Surface'
import type { UsageView } from '@/types/api'

const FORMAT = new Intl.NumberFormat()

export function UsageSummary({ usage }: { usage: UsageView }) {
  return (
    <Surface>
      <dl className="grid grid-cols-3 gap-4">
        <Figure label="Input" value={usage.input_tokens} />
        <Figure label="Output" value={usage.output_tokens} />
        <Figure label="Total" value={usage.total_tokens} emphasis />
      </dl>
    </Surface>
  )
}

function Figure({
  label,
  value,
  emphasis = false,
}: {
  label: string
  value: number
  emphasis?: boolean
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd
        className={
          emphasis
            ? 'mt-1 font-mono text-lg text-ink'
            : 'mt-1 font-mono text-lg text-ink-muted'
        }
      >
        {FORMAT.format(value)}
      </dd>
    </div>
  )
}
