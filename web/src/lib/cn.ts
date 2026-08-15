import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Join class names, letting later ones win.
 *
 * Plain concatenation leaves both `p-2` and `p-4` in the string and the winner
 * is whichever the stylesheet happens to define last — so a component's own
 * padding may or may not override its default depending on build order. This
 * resolves the conflict by intent instead.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
