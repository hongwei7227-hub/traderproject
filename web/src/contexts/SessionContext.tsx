/**
 * Who is signed in, for the tree.
 *
 * The session itself lives in a module, not in this context. That is what lets
 * the API client read it without being a component — and it is why signing out
 * from an interceptor, on a 401, does not need a hook.
 */

import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'

import { configureAuth } from '@/api/client'
import {
  currentSession,
  signOut,
  subscribeToSession,
  type Session,
} from '@/lib/auth'
import { isSoloMode } from '@/lib/env'

interface SessionValue {
  session: Session | null
  signedIn: boolean
  signOut: () => void
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  // Subscribed rather than held in state, so that a sign-out triggered from
  // outside React — the 401 interceptor — re-renders the tree too. State would
  // only update for callers that went through this component.
  const session = useSyncExternalStore(subscribeToSession, currentSession, currentSession)

  useEffect(() => {
    configureAuth({
      getToken: () => Promise.resolve(currentSession()?.token ?? null),
      // No refresh. The login service slides the session's expiry every time
      // the token is used, so a token that is refused is one that has genuinely
      // lapsed — retrying it after a "refresh" that cannot exist would only
      // ask a second time for the same answer.
      refresh: () => {
        signOut()
        return Promise.resolve(null)
      },
    })
  }, [])

  const value = useMemo<SessionValue>(
    () => ({
      session,
      // A single-user deployment has nobody to sign in as. Treating it as
      // signed in is what keeps every guarded route from redirecting to a
      // login page that build does not have.
      signedIn: isSoloMode || session !== null,
      signOut,
    }),
    [session],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (value === null) {
    throw new Error('useSession must be used inside a SessionProvider')
  }
  return value
}
