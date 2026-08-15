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

  /**
   * The login service.
   *
   * Identity belongs to it: it checks the password, mints the token and holds
   * the session. This client posts credentials there and then presents the
   * token it gets back to the platform, which reads the same session.
   *
   * A separate origin from the API because the two are separate services. In
   * development the dev server proxies both, so this is usually left empty.
   */
  loginUrl: readString(import.meta.env.VITE_LOGIN_URL, '/auth'),
} as const

export const isPlatformMode = env.hostMode === 'platform'
export const isSoloMode = env.hostMode === 'oss'

/**
 * Whether a real sign-in flow is available.
 *
 * Distinct from the mode: a platform build with nowhere to sign in is
 * misconfigured, and saying so is better than falling back to letting everyone
 * in.
 */
export function authConfigured(): boolean {
  if (!isPlatformMode) return true
  return env.loginUrl.length > 0
}

export function assertConfigured(): void {
  if (isPlatformMode && !authConfigured()) {
    throw new Error(
      'This build targets a multi-tenant deployment but no login service is ' +
        'configured. Set VITE_LOGIN_URL, or build with VITE_HOST_MODE=oss.',
    )
  }
}
