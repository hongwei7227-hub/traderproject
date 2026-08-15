/**
 * Signing in, against the login service.
 *
 * The token it returns is opaque — a random string whose meaning lives in the
 * service's Redis, not in the token. That has one practical consequence worth
 * knowing here: there is no expiry to read. The client cannot tell a live
 * session from a dead one without asking, so it does not try; a 401 from any
 * request is the signal, and it arrives at the one place that already handles
 * it.
 *
 * The token is kept in memory and mirrored to `sessionStorage` rather than
 * `localStorage`. A shared machine should not keep somebody signed in after
 * the tab is closed, and a trading account is exactly the kind of thing that
 * makes that difference matter.
 */

import { env } from '@/lib/env'

const STORAGE_KEY = 'kairos.session'

export interface Session {
  token: string
  username: string
}

let active: Session | null = restore()
const listeners = new Set<(session: Session | null) => void>()

function restore(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null

    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null

    const record = parsed as Record<string, unknown>
    const token = record['token']
    const username = record['username']
    // Narrowed field by field: what came out of storage is not trusted input,
    // and a half-written value should read as "not signed in" rather than as a
    // session with an undefined token.
    if (typeof token !== 'string' || token.length === 0) return null

    return { token, username: typeof username === 'string' ? username : '' }
  } catch {
    // Storage can be unavailable entirely — a locked-down browser, private
    // mode in some engines. Not being able to remember a session is not a
    // reason to fail to render.
    return null
  }
}

function persist(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // As above: the session still works for this tab, it just will not
    // survive a reload.
  }
}

export function currentSession(): Session | null {
  return active
}

export function setSession(session: Session | null): void {
  active = session
  persist(session)
  for (const listener of listeners) listener(session)
}

export function subscribeToSession(listener: (session: Session | null) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export class SignInFailed extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SignInFailed'
  }
}

interface Envelope {
  success?: unknown
  data?: unknown
  errorMsg?: unknown
}

async function call(path: string, username: string, password: string): Promise<Envelope> {
  const response = await fetch(`${env.loginUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

  if (!response.ok) {
    // The service answers 200 with `success: false` for a bad password, so a
    // non-2xx here is the service itself failing rather than the credentials.
    throw new SignInFailed('The login service could not be reached.')
  }

  const body: unknown = await response.json()
  if (typeof body !== 'object' || body === null) {
    throw new SignInFailed('The login service returned something unexpected.')
  }
  // Every field on `Envelope` is `unknown`, so the narrowing above is all the
  // widening this needs — the callers check each field before using it.
  return body
}

/**
 * Exchange credentials for a session.
 *
 * The failure message is the service's own when it gave one. Substituting a
 * generic "sign-in failed" would hide the difference between a wrong password
 * and an account that does not exist yet — and the second is worth telling
 * someone, since the fix is to register rather than to try again.
 */
export async function signIn(username: string, password: string): Promise<Session> {
  const body = await call('/login', username, password)

  if (body.success === false || typeof body.data !== 'string' || !body.data) {
    throw new SignInFailed(
      typeof body.errorMsg === 'string' && body.errorMsg
        ? body.errorMsg
        : 'Those credentials were not accepted.',
    )
  }

  const session: Session = { token: body.data, username }
  setSession(session)
  return session
}

export async function register(username: string, password: string): Promise<void> {
  const body = await call('/register', username, password)
  if (body.success === false) {
    throw new SignInFailed(
      typeof body.errorMsg === 'string' && body.errorMsg
        ? body.errorMsg
        : 'That account could not be created.',
    )
  }
}

export function signOut(): void {
  setSession(null)
}
