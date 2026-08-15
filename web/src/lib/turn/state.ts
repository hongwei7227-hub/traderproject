/**
 * Turning a sequence of events into something renderable.
 *
 * A turn arrives as an ordered stream and has to become a structure the
 * interface can draw and redraw cheaply. The reduction is a pure function of
 * the events so far, which is what makes a replayed turn render identically to
 * a live one: both are the same fold over the same sequence.
 *
 * Events may arrive twice. A reconnect resumes from a cursor, and the boundary
 * is inclusive because the event at the cursor may have been incomplete when
 * it was last seen. So the reducer is idempotent per sequence number rather
 * than assuming each event arrives once.
 */

import type { TurnEvent } from '@/types/api'

export type Phase = 'idle' | 'thinking' | 'acting' | 'done' | 'failed'

export interface ToolCall {
  callId: string
  name: string
  arguments: Record<string, unknown>
  /** Absent while the call is still running. */
  result?: { ok: boolean; summary: string }
}

export interface Artifact {
  kind: string
  ref: string
  title?: string
}

export interface Notice {
  message: string
  severity: 'info' | 'warning'
}

export interface TurnState {
  runId: string | null
  phase: Phase
  /** The answer, accumulated from its chunks. */
  text: string
  /** The model's working, where the provider exposes it. */
  reasoning: string
  toolCalls: ToolCall[]
  artifacts: Artifact[]
  notices: Notice[]
  usage: { input: number; output: number; model: string } | null
  error: { message: string; retryable: boolean } | null
  /** Why the turn stopped, once it has. */
  stopReason: string | null
  /** Highest sequence folded in, so duplicates can be recognised. */
  lastSeq: number
}

export function emptyTurn(): TurnState {
  return {
    runId: null,
    phase: 'idle',
    text: '',
    reasoning: '',
    toolCalls: [],
    artifacts: [],
    notices: [],
    usage: null,
    error: null,
    stopReason: null,
    lastSeq: -1,
  }
}

/**
 * Whether the turn stopped short of answering.
 *
 * A budget-exhausted turn returns prose that reads like a finished answer.
 * Presenting it as one is misleading, so the distinction is surfaced rather
 * than left for a caller to infer from the reason string.
 */
export function wasTruncated(state: TurnState): boolean {
  return (
    state.stopReason !== null &&
    state.stopReason !== 'answered' &&
    state.stopReason !== 'cancelled'
  )
}

export function isSettled(state: TurnState): boolean {
  return state.phase === 'done' || state.phase === 'failed'
}

/**
 * Fold one event into the state.
 *
 * Returns the same object when nothing changed, so a caller comparing by
 * identity can skip a re-render.
 */
export function reduceTurn(state: TurnState, event: TurnEvent): TurnState {
  // A resumed stream re-delivers the event at the cursor, because that event
  // may have been mid-flight when the connection dropped. Text is the case
  // that matters: appending it twice duplicates a sentence in the answer.
  if (event.seq <= state.lastSeq) return state

  const next: TurnState = { ...state, lastSeq: event.seq }

  switch (event.kind) {
    case 'metadata':
      next.runId = event.payload.run_id
      next.phase = 'thinking'
      return next

    case 'text':
      next.text = state.text + event.payload.text
      return next

    case 'reasoning':
      next.reasoning = state.reasoning + event.payload.text
      return next

    case 'tool_call':
      next.phase = 'acting'
      next.toolCalls = [
        ...state.toolCalls,
        {
          callId: event.payload.call_id,
          name: event.payload.name,
          arguments: event.payload.arguments,
        },
      ]
      return next

    case 'tool_result': {
      // Matched by call id rather than by position: results need not arrive in
      // the order the calls were announced, and a positional match would
      // attach one call's outcome to another's card.
      const index = state.toolCalls.findIndex(
        (call) => call.callId === event.payload.call_id,
      )
      if (index === -1) {
        // A result for a call this client never saw — it reconnected after the
        // call was announced. Rendered on its own rather than dropped, so the
        // reader sees that something ran.
        next.toolCalls = [
          ...state.toolCalls,
          {
            callId: event.payload.call_id,
            name: event.payload.call_id,
            arguments: {},
            result: { ok: event.payload.ok, summary: event.payload.summary },
          },
        ]
        return next
      }
      const updated = [...state.toolCalls]
      updated[index] = {
        ...updated[index]!,
        result: { ok: event.payload.ok, summary: event.payload.summary },
      }
      next.toolCalls = updated
      next.phase = 'thinking'
      return next
    }

    case 'artifact':
      next.artifacts = [
        ...state.artifacts,
        {
          kind: event.payload.artifact_kind,
          ref: event.payload.ref,
          ...(event.payload.title ? { title: event.payload.title } : {}),
        },
      ]
      return next

    case 'usage':
      // Cumulative, so the latest replaces rather than adds to the previous.
      // Summing would count every earlier report again.
      next.usage = {
        input: event.payload.input_tokens,
        output: event.payload.output_tokens,
        model: event.payload.model,
      }
      return next

    case 'notice':
      next.notices = [
        ...state.notices,
        { message: event.payload.message, severity: event.payload.severity },
      ]
      return next

    case 'error':
      next.phase = 'failed'
      next.error = {
        message: event.payload.message,
        retryable: event.payload.retryable ?? false,
      }
      return next

    case 'done':
      next.phase = 'done'
      next.stopReason = event.payload.reason
      return next
  }
}

export function reduceAll(state: TurnState, events: readonly TurnEvent[]): TurnState {
  return events.reduce(reduceTurn, state)
}
