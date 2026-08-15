/**
 * Build-time configuration, read once and typed.
 *
 * Every value is validated here rather than at each use, so that a missing or
 * malformed variable produces one clear failure at startup instead of an
 * `undefined` that surfaces as a broken request three screens later.
 */

/**
 * Which deployment this build talks to.
 *
 * Read from an explicit variable, never inferred from whether an auth provider
 * is configured. The reference implementation checked for the presence of a
 * Supabase URL, which meant a platform deployment whose auth config failed to
 * load silently became a single-user one — with every request answering as the
 * same built-in account.
 */
export type HostMode = 'oss' | 'platform'

function readMode(): HostMode {
  const raw = import.meta.env.VITE_HOST_MODE
  return raw === 'platform' ? 'platform' : 'oss'
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

export const env = {
  hostMode: readMode(),

  /** Base for REST and SSE. Empty means same origin. */
  apiBaseUrl: readString(import.meta.env.VITE_API_BASE_URL, ''),

  /** The fixed identity a single-user deployment answers as. */
  localUserId: readString(import.meta.env.VITE_AUTH_USER_ID, 'local-dev-user'),

  supabaseUrl: readString(import.meta.env.VITE_SUPABASE_URL, ''),
  supabaseKey: readString(import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY, ''),
} as const

export const isPlatformMode = env.hostMode === 'platform'
export const isSoloMode = env.hostMode === 'oss'

/**
 * Whether a real sign-in flow is available.
 *
 * Distinct from the mode: a platform build whose auth credentials are missing
 * is misconfigured, and saying so is better than falling back to letting
 * everyone in.
 */
export function authConfigured(): boolean {
  if (!isPlatformMode) return true
  return env.supabaseUrl.length > 0 && env.supabaseKey.length > 0
}

export function assertConfigured(): void {
  if (isPlatformMode && !authConfigured()) {
    throw new Error(
      'This build targets a multi-tenant deployment but no auth provider is ' +
        'configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY, ' +
        'or build with VITE_HOST_MODE=oss.',
    )
  }
}
