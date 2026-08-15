import { cva, type VariantProps } from 'class-variance-authority'
import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

const button = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'rounded font-medium transition-colors',
    // Visible only for keyboard users, which is who needs it. A ring on every
    // click makes people remove it entirely, and then nobody has one.
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
    'focus-visible:ring-offset-2 focus-visible:ring-offset-canvas',
    'disabled:pointer-events-none disabled:opacity-50',
  ],
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-ink hover:opacity-90',
        secondary: 'bg-raised text-ink border border-border hover:bg-canvas',
        ghost: 'text-ink-muted hover:bg-raised hover:text-ink',
        danger: 'bg-negative text-white hover:opacity-90',
      },
      size: {
        sm: 'h-8 px-3 text-sm',
        md: 'h-9 px-4 text-sm',
        lg: 'h-11 px-6 text-base',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  /**
   * Shows a busy state and blocks further clicks.
   *
   * Separate from `disabled` because the two mean different things to a screen
   * reader: disabled says "not available", busy says "working on it".
   */
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading = false, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(button({ variant, size }), className)}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
          // Decorative: the busy state is already announced by aria-busy, and
          // announcing it twice is noise.
          aria-hidden="true"
        />
      )}
      {children}
    </button>
  )
})
