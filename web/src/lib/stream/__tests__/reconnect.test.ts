/**
 * The cursor rules, and the generation guard.
 *
 * Each of these encodes a way a reconnect can make things worse than the
 * disconnect it was recovering from.
 */

import { describe, expect, it } from 'vitest'

import type { StreamEvent } from '../reader'
import { backoffFor, Generation, MAX_ATTEMPTS, retrySchedule, StreamCursor } from '../reconnect'

function event(id: number | null, kind = 'text'): StreamEvent {
  return { kind, id, data: {} }
}

describe('cursor', () => {
  it('starts empty', () => {
    const cursor = new StreamCursor()
    expect(cursor.lastEventId).toBeNull()
    expect(cursor.runId).toBeNull()
  })

  it('advances on main-trunk events', () => {
    const cursor = new StreamCursor()
    cursor.observe(event(3))
    cursor.observe(event(4))
    expect(cursor.lastEventId).toBe(4)
  })

  it('ignores sub-task events', () => {
    // A sub-task is numbered in its own space. Letting one advance the shared
    // cursor resumes the main stream from a position it never had.
    const cursor = new StreamCursor()
    cursor.observe(event(5))
    cursor.observe(event(99), { subTask: true })
    expect(cursor.lastEventId).toBe(5)
  })

  it('ignores events with no id', () => {
    const cursor = new StreamCursor()
    cursor.observe(event(2))
    cursor.observe(event(null))
    expect(cursor.lastEventId).toBe(2)
  })

  it('latches the run id from the reconnect location', () => {
    const cursor = new StreamCursor()
    cursor.latchFromContentLocation('/api/v1/threads/t/messages/stream?run_id=r-42')
    expect(cursor.runId).toBe('r-42')
  })

  it('ignores a location with no run id', () => {
    const cursor = new StreamCursor()
    cursor.latchFromContentLocation('/somewhere/else')
    expect(cursor.runId).toBeNull()
  })

  it('url-decodes the latched run id', () => {
    const cursor = new StreamCursor()
    cursor.latchFromContentLocation('/s?run_id=a%2Fb')
    expect(cursor.runId).toBe('a/b')
  })
})

describe('resetting', () => {
  it('clears position when attaching to a different run', () => {
    // Opening another thread while the cursor still points at the previous
    // one attaches to a dead stream: no live events, and the content only
    // appears on a later refetch.
    const cursor = new StreamCursor()
    cursor.observe(event(10))
    cursor.reset('r-new')

    expect(cursor.lastEventId).toBeNull()
    expect(cursor.runId).toBe('r-new')
  })

  it('replay clears the position but keeps the run', () => {
    // Replayed events are numbered in their own space; writing one into the
    // live cursor makes the next reconnect resume from an unrelated offset.
    const cursor = new StreamCursor()
    cursor.reset('r-1')
    cursor.observe(event(8))
    cursor.beginReplay()

    expect(cursor.lastEventId).toBeNull()
    expect(cursor.runId).toBe('r-1')
  })

  it('a mid-turn reconnect keeps its position', () => {
    // Resetting here would resume from the beginning and re-deliver
    // everything the reader already saw.
    const cursor = new StreamCursor()
    cursor.latchFromContentLocation('/s?run_id=r-1')
    cursor.observe(event(12))

    expect(cursor.lastEventId).toBe(12)
    expect(cursor.runId).toBe('r-1')
  })
})

describe('backoff', () => {
  it('does not wait before the first attempt', () => {
    expect(backoffFor(0)).toBe(0)
  })

  it('doubles each time', () => {
    expect([1, 2, 3, 4, 5].map(backoffFor)).toEqual([1000, 2000, 4000, 8000, 16000])
  })

  it('schedules a bounded number of attempts', () => {
    const plans = [...retrySchedule()]
    expect(plans).toHaveLength(MAX_ATTEMPTS)
    expect(plans.at(-1)?.final).toBe(true)
  })

  it('totals roughly half a minute before giving up', () => {
    // Long enough to ride out a tunnel, short enough that a reader is not
    // left staring at a dead pane.
    const total = [...retrySchedule()].reduce((sum, plan) => sum + plan.delayMs, 0)
    expect(total).toBe(31_000)
  })
})

describe('generation guard', () => {
  it('recognises the current attempt', () => {
    const generation = new Generation()
    const token = generation.next()
    expect(generation.isCurrent(token)).toBe(true)
  })

  it('discards an attempt a newer one superseded', () => {
    // A reader who navigates away and back starts a newer attempt. Without
    // this, the older one resolves later and reattaches the stream to a
    // thread nobody is looking at.
    const generation = new Generation()
    const stale = generation.next()
    generation.next()

    expect(generation.isCurrent(stale)).toBe(false)
  })

  it('can invalidate everything outstanding', () => {
    const generation = new Generation()
    const token = generation.next()
    generation.invalidate()

    expect(generation.isCurrent(token)).toBe(false)
  })
})
