/**
 * Signing in against the login service.
 *
 * The token is opaque — a random string whose meaning lives in the service's
 * Redis. So the cases worth holding are about handling something the client
 * cannot inspect: a refusal that arrives as a 200, storage that is unavailable,
 * and a half-written session that must read as "not signed in" rather than as
 * a session with an undefined token.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { currentSession, register, signIn, signOut, SignInFailed } from '../auth'

const STORAGE_KEY = 'kairos.session'

function answers(body: unknown, ok = true): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok,
        json: () => Promise.resolve(body),
      } as Response),
    ),
  )
}

beforeEach(() => {
  sessionStorage.clear()
  signOut()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('signing in', () => {
  it('keeps the token the service minted', async () => {
    answers({ success: true, data: 'a1b2c3' })
    const session = await signIn('alice', 'hunter2')

    expect(session.token).toBe('a1b2c3')
    expect(currentSession()?.username).toBe('alice')
  })

  it('survives a reload within the tab', async () => {
    answers({ success: true, data: 'a1b2c3' })
    await signIn('alice', 'hunter2')

    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '{}')).toMatchObject({
      token: 'a1b2c3',
    })
  })

  it('uses sessionStorage rather than localStorage', async () => {
    // A shared machine should not keep someone signed into a trading account
    // after the tab is closed.
    answers({ success: true, data: 'a1b2c3' })
    await signIn('alice', 'hunter2')

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('reports the service’s own reason for refusing', async () => {
    // "No such account" and "wrong password" call for different actions, and
    // only the first is worth a trip to the register form.
    answers({ success: false, errorMsg: 'No such account' })

    await expect(signIn('alice', 'wrong')).rejects.toThrow('No such account')
  })

  it('treats a refusal as not signed in', async () => {
    answers({ success: false, errorMsg: 'nope' })
    await expect(signIn('alice', 'wrong')).rejects.toThrow(SignInFailed)

    expect(currentSession()).toBeNull()
  })

  it('distinguishes the service being down from bad credentials', async () => {
    // It answers 200 with success:false for a bad password, so a non-2xx is
    // the service itself failing.
    answers(null, false)

    await expect(signIn('alice', 'hunter2')).rejects.toThrow(/could not be reached/i)
  })

  it('refuses an answer with no token in it', async () => {
    answers({ success: true, data: null })
    await expect(signIn('alice', 'hunter2')).rejects.toThrow(SignInFailed)
  })
})

describe('registering', () => {
  it('reports why an account could not be created', async () => {
    answers({ success: false, errorMsg: 'Username taken' })
    await expect(register('alice', 'hunter2')).rejects.toThrow('Username taken')
  })

  it('does not sign anyone in by itself', async () => {
    // The service mints a token only on login.
    answers({ success: true, data: null })
    await register('alice', 'hunter2')

    expect(currentSession()).toBeNull()
  })
})

describe('restoring', () => {
  it('ignores a stored value with no token', () => {
    // Storage is not trusted input. A half-written value should read as "not
    // signed in", not as a session whose token is undefined.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ username: 'alice' }))
    expect(currentSession()).toBeNull()
  })

  it('ignores unparseable storage', () => {
    sessionStorage.setItem(STORAGE_KEY, 'not json')
    expect(currentSession()).toBeNull()
  })
})

describe('signing out', () => {
  it('forgets the session', async () => {
    answers({ success: true, data: 'a1b2c3' })
    await signIn('alice', 'hunter2')

    signOut()

    expect(currentSession()).toBeNull()
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})
