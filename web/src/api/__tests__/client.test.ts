/**
 * Error shaping and the refresh-once rule.
 */

import MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, configureAuth, createClient } from '../client'

let client: ReturnType<typeof createClient>
let mock: MockAdapter

beforeEach(() => {
  client = createClient('')
  mock = new MockAdapter(client)
  configureAuth({ getToken: async () => null, refresh: async () => null })
})

afterEach(() => {
  mock.restore()
})

describe('authorization', () => {
  it('attaches a bearer token when one is available', async () => {
    configureAuth({ getToken: async () => 'tok-1' })
    mock.onGet('/x').reply((config) => {
      expect(config.headers?.Authorization).toBe('Bearer tok-1')
      return [200, {}]
    })

    await client.get('/x')
  })

  it('sends no header when there is no token', async () => {
    mock.onGet('/x').reply((config) => {
      expect(config.headers?.Authorization).toBeUndefined()
      return [200, {}]
    })

    await client.get('/x')
  })
})

describe('refreshing', () => {
  it('refreshes once and replays the request', async () => {
    let calls = 0
    configureAuth({ getToken: async () => 'stale', refresh: async () => 'fresh' })

    mock.onGet('/x').reply((config) => {
      calls += 1
      if (calls === 1) return [401, { detail: 'expired' }]
      expect(config.headers?.Authorization).toBe('Bearer fresh')
      return [200, { ok: true }]
    })

    const response = await client.get('/x')

    expect(calls).toBe(2)
    expect(response.data).toEqual({ ok: true })
  })

  it('does not loop when the refreshed token is also refused', async () => {
    // A second 401 means the credentials are wrong rather than stale.
    // Retrying again only asks a server that has already refused twice.
    let calls = 0
    configureAuth({ getToken: async () => 'a', refresh: async () => 'b' })
    mock.onGet('/x').reply(() => {
      calls += 1
      return [401, { detail: 'nope' }]
    })

    await expect(client.get('/x')).rejects.toBeInstanceOf(ApiError)
    expect(calls).toBe(2)
  })

  it('gives up when refreshing fails', async () => {
    configureAuth({
      getToken: async () => 'a',
      refresh: async () => {
        throw new Error('refresh endpoint down')
      },
    })
    mock.onGet('/x').reply(401, { detail: 'expired' })

    await expect(client.get('/x')).rejects.toMatchObject({ status: 401 })
  })
})

describe('error shaping', () => {
  it('reads a structured detail', async () => {
    mock.onGet('/x').reply(403, {
      detail: {
        message: 'No provider configured.',
        type: 'no_provider',
        link: { url: '/setup', label: 'Set one up' },
      },
    })

    await expect(client.get('/x')).rejects.toMatchObject({
      status: 403,
      kind: 'no_provider',
      message: 'No provider configured.',
      action: { url: '/setup', label: 'Set one up' },
    })
  })

  it('reads a plain string detail', async () => {
    // The server sends both shapes; no call site should have to know which.
    mock.onGet('/x').reply(404, { detail: 'Thread not found' })

    await expect(client.get('/x')).rejects.toMatchObject({
      status: 404,
      message: 'Thread not found',
    })
  })

  it('carries the retry delay from a structured body', async () => {
    mock.onGet('/x').reply(429, {
      detail: { message: 'Slow down', type: 'burst_limit', retry_after: 5 },
    })

    await expect(client.get('/x')).rejects.toMatchObject({ retryAfter: 5 })
  })

  it('falls back to the retry-after header', async () => {
    mock.onGet('/x').reply(429, {}, { 'retry-after': '12' })

    await expect(client.get('/x')).rejects.toMatchObject({
      retryAfter: 12,
      kind: 'rate_limited',
    })
  })

  it('reports an unreachable server distinctly', async () => {
    // "Could not reach the server" is actionable; a generic failure is not.
    mock.onGet('/x').networkError()

    await expect(client.get('/x')).rejects.toMatchObject({
      status: 0,
      kind: 'network',
    })
  })
})

describe('retryability', () => {
  it.each([429, 500, 503])('treats %i as worth retrying', (status) => {
    expect(new ApiError(status, 'x').retryable).toBe(true)
  })

  it.each([400, 401, 403, 404, 422])('treats %i as final', (status) => {
    // The server refused on the merits. Sending it again asks the same
    // question and gets the same answer.
    expect(new ApiError(status, 'x').retryable).toBe(false)
  })
})
