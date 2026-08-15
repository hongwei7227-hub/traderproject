/**
 * The fold, and the duplicate-delivery rule that makes reconnection safe.
 */

import { describe, expect, it } from 'vitest'

import type { TurnEvent } from '@/types/api'

import {
  emptyTurn,
  isSettled,
  reduceAll,
  reduceTurn,
  wasTruncated,
  type TurnState,
} from '../state'

let seq = 0
function next(): number {
  seq += 1
  return seq
}

function metadata(runId = 'run-1'): TurnEvent {
  return { kind: 'metadata', seq: next(), payload: { run_id: runId } }
}
function text(value: string): TurnEvent {
  return { kind: 'text', seq: next(), payload: { text: value } }
}
function toolCall(callId: string, name = 'search'): TurnEvent {
  return { kind: 'tool_call', seq: next(), payload: { call_id: callId, name, arguments: {} } }
}
function toolResult(callId: string, ok = true): TurnEvent {
  return {
    kind: 'tool_result',
    seq: next(),
    payload: { call_id: callId, ok, summary: `${callId} finished` },
  }
}
function usage(input: number, output: number): TurnEvent {
  return {
    kind: 'usage',
    seq: next(),
    payload: { input_tokens: input, output_tokens: output, model: 'big' },
  }
}
function done(reason = 'answered'): TurnEvent {
  return { kind: 'done', seq: next(), payload: { phase: 'completed', reason } }
}

function fresh(): TurnState {
  seq = 0
  return emptyTurn()
}

describe('accumulation', () => {
  it('joins text chunks in order', () => {
    const state = reduceAll(fresh(), [
      metadata(),
      text('Hello'),
      text(', '),
      text('world'),
      done(),
    ])
    expect(state.text).toBe('Hello, world')
  })

  it('keeps reasoning separate from the answer', () => {
    // Clients render them differently, and some suppress reasoning entirely.
    const state = reduceAll(fresh(), [
      metadata(),
      { kind: 'reasoning', seq: next(), payload: { text: 'considering' } },
      text('answer'),
    ])
    expect(state.reasoning).toBe('considering')
    expect(state.text).toBe('answer')
  })

  it('latches the run id from metadata', () => {
    expect(reduceAll(fresh(), [metadata('run-9')]).runId).toBe('run-9')
  })
})

describe('duplicate delivery', () => {
  it('ignores an event already folded in', () => {
    // A resumed stream re-delivers the event at the cursor, because it may
    // have been mid-flight when the connection dropped. Appending twice
    // duplicates a sentence in the answer.
    const start = reduceAll(fresh(), [metadata(), text('once')])
    const repeated = reduceTurn(start, { kind: 'text', seq: 2, payload: { text: 'once' } })

    expect(repeated.text).toBe('once')
  })

  it('returns the same object when nothing changed', () => {
    // Identity comparison lets a caller skip a re-render.
    const start = reduceAll(fresh(), [metadata(), text('a')])
    expect(reduceTurn(start, { kind: 'text', seq: 1, payload: { text: 'a' } })).toBe(start)
  })

  it('accepts events after the cursor', () => {
    const start = reduceAll(fresh(), [metadata(), text('a')])
    expect(reduceTurn(start, { kind: 'text', seq: 99, payload: { text: 'b' } }).text).toBe(
      'ab',
    )
  })
})

describe('tool calls', () => {
  it('pairs a result with its call by id', () => {
    const state = reduceAll(fresh(), [
      metadata(),
      toolCall('c1', 'search'),
      toolCall('c2', 'fetch'),
      toolResult('c2'),
    ])

    expect(state.toolCalls[0]?.result).toBeUndefined()
    expect(state.toolCalls[1]?.result?.summary).toContain('c2')
  })

  it('does not match by position', () => {
    // Results need not arrive in the order calls were announced; a positional
    // match attaches one call's outcome to another's card.
    const state = reduceAll(fresh(), [
      metadata(),
      toolCall('slow'),
      toolCall('fast'),
      toolResult('fast'),
      toolResult('slow'),
    ])

    expect(state.toolCalls[0]?.result?.summary).toContain('slow')
    expect(state.toolCalls[1]?.result?.summary).toContain('fast')
  })

  it('renders a result whose call was never seen', () => {
    // A client that reconnected after the call was announced. Showing it
    // beats dropping it, because something did run.
    const state = reduceAll(fresh(), [metadata(), toolResult('orphan')])
    expect(state.toolCalls).toHaveLength(1)
    expect(state.toolCalls[0]?.result?.ok).toBe(true)
  })

  it('records a failed call without losing it', () => {
    const state = reduceAll(fresh(), [metadata(), toolCall('c1'), toolResult('c1', false)])
    expect(state.toolCalls[0]?.result?.ok).toBe(false)
  })
})

describe('phase', () => {
  it('moves through thinking and acting', () => {
    let state = reduceAll(fresh(), [metadata()])
    expect(state.phase).toBe('thinking')

    state = reduceTurn(state, toolCall('c1'))
    expect(state.phase).toBe('acting')

    state = reduceTurn(state, toolResult('c1'))
    expect(state.phase).toBe('thinking')

    state = reduceTurn(state, done())
    expect(state.phase).toBe('done')
  })

  it('an error is terminal', () => {
    const state = reduceAll(fresh(), [
      metadata(),
      { kind: 'error', seq: next(), payload: { message: 'upstream refused' } },
    ])
    expect(state.phase).toBe('failed')
    expect(isSettled(state)).toBe(true)
  })

  it('a live turn is not settled', () => {
    expect(isSettled(reduceAll(fresh(), [metadata(), text('partial')]))).toBe(false)
  })
})

describe('usage', () => {
  it('replaces rather than accumulates', () => {
    // Usage is cumulative on the wire. Summing would count every earlier
    // report again.
    const state = reduceAll(fresh(), [metadata(), usage(100, 10), usage(100, 90)])
    expect(state.usage).toEqual({ input: 100, output: 90, model: 'big' })
  })

  it('is absent until reported', () => {
    expect(reduceAll(fresh(), [metadata()]).usage).toBeNull()
  })
})

describe('truncation', () => {
  it('an answered turn is not truncated', () => {
    expect(wasTruncated(reduceAll(fresh(), [metadata(), done('answered')]))).toBe(false)
  })

  it('a budget-stopped turn is', () => {
    // It returns prose that reads like a finished answer; presenting it as
    // one is misleading.
    expect(wasTruncated(reduceAll(fresh(), [metadata(), done('iteration_budget')]))).toBe(
      true,
    )
  })

  it('a cancelled turn is not', () => {
    // The reader stopped it and knows why.
    expect(wasTruncated(reduceAll(fresh(), [metadata(), done('cancelled')]))).toBe(false)
  })

  it('a live turn is not', () => {
    expect(wasTruncated(reduceAll(fresh(), [metadata(), text('going')]))).toBe(false)
  })
})

describe('notices and artifacts', () => {
  it('collects notices in order with their severity', () => {
    const state = reduceAll(fresh(), [
      metadata(),
      { kind: 'notice', seq: next(), payload: { message: 'fell back', severity: 'warning' } },
    ])
    expect(state.notices[0]).toEqual({ message: 'fell back', severity: 'warning' })
  })

  it('collects artifacts by reference, not by value', () => {
    const state = reduceAll(fresh(), [
      metadata(),
      {
        kind: 'artifact',
        seq: next(),
        payload: { artifact_kind: 'chart', ref: 's3://b/c.png', title: 'Revenue' },
      },
    ])
    expect(state.artifacts[0]).toEqual({
      kind: 'chart',
      ref: 's3://b/c.png',
      title: 'Revenue',
    })
  })
})

describe('replay equivalence', () => {
  it('a replayed turn folds to the same state as a live one', () => {
    // The property that makes replay trustworthy: both are the same fold over
    // the same sequence, so a turn watched live and one opened tomorrow must
    // render identically.
    const events = [
      metadata('run-7'),
      text('Revenue '),
      toolCall('c1'),
      toolResult('c1'),
      text('grew 12%.'),
      usage(500, 120),
      done(),
    ]

    const live = events.reduce(reduceTurn, emptyTurn())
    const replayed = reduceAll(emptyTurn(), events)

    expect(replayed).toEqual(live)
  })
})
