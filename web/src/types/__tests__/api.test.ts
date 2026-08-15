/**
 * Narrowing raw frames, including the malformed ones a wire protocol produces.
 */

import { describe, expect, it } from 'vitest'

import { asTurnEvent } from '../api'

function raw(kind: string, data: Record<string, unknown> = {}, id: number | null = 1) {
  return { kind, id, data }
}

describe('known kinds', () => {
  it('narrows metadata', () => {
    const event = asTurnEvent(raw('metadata', { run_id: 'r-1', model: 'big' }))
    expect(event).toEqual({
      kind: 'metadata',
      seq: 1,
      payload: { run_id: 'r-1', model: 'big' },
    })
  })

  it('narrows text and reasoning to the same shape', () => {
    expect(asTurnEvent(raw('text', { text: 'hi' }))?.payload).toEqual({ text: 'hi' })
    expect(asTurnEvent(raw('reasoning', { text: 'hm' }))?.payload).toEqual({ text: 'hm' })
  })

  it('narrows a tool call with its arguments', () => {
    const event = asTurnEvent(
      raw('tool_call', { call_id: 'c1', name: 'search', arguments: { q: 'x' } }),
    )
    expect(event?.payload).toEqual({
      call_id: 'c1',
      name: 'search',
      arguments: { q: 'x' },
    })
  })

  it('narrows usage as numbers', () => {
    const event = asTurnEvent(
      raw('usage', { input_tokens: 10, output_tokens: 5, model: 'm' }),
    )
    expect(event?.payload).toEqual({ input_tokens: 10, output_tokens: 5, model: 'm' })
  })
})

describe('unknown kinds', () => {
  it('returns null rather than breaking', () => {
    // A newer server may emit kinds this build predates. Ignoring one beats
    // failing on it; the kinds that matter have been in the protocol always.
    expect(asTurnEvent(raw('telemetry_v9'))).toBeNull()
  })
})

describe('malformed payloads', () => {
  it('substitutes a default for a missing string', () => {
    // A blanket assertion would compile and hand the interface an undefined
    // that surfaces three components later.
    expect(asTurnEvent(raw('text', {}))?.payload).toEqual({ text: '' })
  })

  it('substitutes a default for a wrongly typed number', () => {
    const event = asTurnEvent(raw('usage', { input_tokens: 'lots', output_tokens: 5 }))
    expect(event?.payload).toMatchObject({ input_tokens: 0, output_tokens: 5 })
  })

  it('rejects a non-object arguments field', () => {
    const event = asTurnEvent(raw('tool_call', { call_id: 'c', arguments: 'nope' }))
    expect(event?.payload).toMatchObject({ arguments: {} })
  })

  it('rejects an array as an arguments object', () => {
    const event = asTurnEvent(raw('tool_call', { arguments: [1, 2] }))
    expect(event?.payload).toMatchObject({ arguments: {} })
  })

  it('falls back to info for an unrecognised severity', () => {
    const event = asTurnEvent(raw('notice', { message: 'm', severity: 'catastrophe' }))
    expect(event?.payload).toMatchObject({ severity: 'info' })
  })

  it('gives an error without a message something to show', () => {
    // An empty error banner is worse than a generic one.
    expect(asTurnEvent(raw('error', {}))?.payload).toMatchObject({
      message: 'Something went wrong.',
    })
  })

  it('omits optional fields rather than setting them undefined', () => {
    const event = asTurnEvent(raw('artifact', { artifact_kind: 'chart', ref: 's3://x' }))
    expect(event?.payload).toEqual({ artifact_kind: 'chart', ref: 's3://x' })
    expect('title' in (event?.payload ?? {})).toBe(false)
  })
})

describe('sequence', () => {
  it('uses the frame id', () => {
    expect(asTurnEvent(raw('text', { text: 'x' }, 7))?.seq).toBe(7)
  })

  it('defaults to zero when the frame carried no id', () => {
    expect(asTurnEvent(raw('text', { text: 'x' }, null))?.seq).toBe(0)
  })
})
