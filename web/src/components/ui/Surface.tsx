import { cva, type VariantProps } from 'class-variance-authority'
import { forwardRef, type HTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

const surface = cva('rounded-lg', {
  variants: {
    // Elevation is a role, not a shadow value. A component says how far from
    // the page it sits and the theme decides what that looks like — which is
    // why the dark palette can lean on borders where the light one leans on
    // shadow.
    elevation: {
      flat: 'bg-surface border border-border',
      raised: 'bg-raised border border-border shadow-[var(--shadow-raised)]',
      overlay: 'bg-raised border border-border shadow-[var(--shadow-overlay)]',
    },
    padding: {
      none: '',
      sm: 'p-3',
      md: 'p-4',
      lg: 'p-6',
    },
  },
  defaultVariants: { elevation: 'flat', padding: 'md' },
})

export interface SurfaceProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof surface> {}

export const Surface = forwardRef<HTMLDivElement, SurfaceProps>(function Surface(
  { className, elevation, padding, ...props },
  ref,
) {
  return (
    <div ref={ref} className={cn(surface({ elevation, padding }), className)} {...props} />
  )
})
