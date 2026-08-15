/**
 * Getting back onto a stream that dropped.
 *
 * Three things have to be right or a reconnect makes matters worse than the
 * disconnect did.
 *
 * The cursor must point at the right stream. It advances only on main-trunk
 * events; a sub-task carries its own sequence space, and letting one of those
 * advance the shared cursor resumes the main stream from a position that never
 * existed on it.
 *
 * A replay must not touch the cursor at all. Replayed events are numbered in
 * their own space, so writing one into the live cursor makes the next
 * reconnect resume from an unrelated offset.
 *
 * And an attempt must know whether it is still wanted. A user who navigates
 * away and back starts a newer attempt; without a generation check the older
 * one finishes later and reattaches the stream to a thread nobody is looking
 * at.
 */

import { readStream, type StreamEvent, type StreamOutcome } from './reader'

/** 1s, 2s, 4s, 8s, 16s — then give up and reload from history. */
export const MAX_ATTEMPTS = 5
const BASE_DELAY_MS = 1_000

export function backoffFor(attempt: number): number {
  return attempt <= 0 ? 0 : BASE_DELAY_MS * 2 ** (attempt - 1)
}

/**
 * Tracks where a live stream has got to.
 *
 * Deliberately a small object rather than loose refs: the two rules about what
 * may write to it are easier to keep true when there is one place that can.
 */
export class StreamCursor {
  #lastEventId: number | null = null
  #runId: string | null = null

  get lastEventId(): number | null {
    return this.#lastEventId
  }

  get runId(): string | null {
    return this.#runId
  }

  /**
   * Record an event from the live stream.
   *
   * Sub-task events are ignored: they are numbered per task, and advancing the
   * shared cursor with one resumes the main stream from a position it never
   * had.
   */
  observe(event: StreamEvent, options: { subTask?: boolean } = {}): void {
    if (options.subTask) return
    if (event.id !== null) this.#lastEventId = event.id
  }

  /** Latch the run this cursor follows, from the reconnect URL. */
  latchFromContentLocation(contentLocation: string | null): void {
    if (!contentLocation) return
    const match = /[?&]run_id=([^&]+)/.exec(contentLocation)
    if (match?.[1]) this.#runId = decodeURIComponent(match[1])
  }

  /**
   * Point at a different run, discarding the previous position.
   *
   * For callers attaching to a thread's current run — opening a thread,
   * navigating between them, resuming after an interruption. Reconnecting
   * mid-turn must *not* reset, or it resumes from the beginning and delivers
   * everything the reader already saw.
   */
  reset(runId: string | null = null): void {
    this.#lastEventId = null
    this.#runId = runId
  }

  /** Replayed history is numbered separately and must not move the cursor. */
  beginReplay(): void {
    this.#lastEventId = null
  }
}

export interface ReconnectOptions {
  threadId: string
  cursor: StreamCursor
  signal?: AbortSignal
  onEvent: (event: StreamEvent) => void
  headers?: Record<string, string>
}

/**
 * Reattach to a run's stream from where this client left off.
 *
 * The cursor travels as a query parameter rather than the `Last-EventID`
 * header the standard specifies. Nothing sends that header here — this is
 * `fetch`, not `EventSource`, precisely so the request can carry an
 * authorization header — and the server's contract chose the parameter so a
 * resume is visible in an access log when one has to be debugged.
 */
export async function reconnect(options: ReconnectOptions): Promise<StreamOutcome> {
  const params = new URLSearchParams()
  if (options.cursor.runId) params.set('run_id', options.cursor.runId)
  if (options.cursor.lastEventId !== null) {
    params.set('last_event_id', String(options.cursor.lastEventId))
  }

  const query = params.toString()
  const url = `/api/v1/threads/${options.threadId}/messages/stream${query ? `?${query}` : ''}`

  return readStream(
    url,
    {
      method: 'GET',
      headers: options.headers ?? {},
      ...(options.signal ? { signal: options.signal } : {}),
    },
    {
      onEvent: (event) => {
        options.cursor.observe(event)
        options.onEvent(event)
      },
    },
  )
}

/**
 * A monotonic token identifying the current attempt.
 *
 * Held by whoever owns the stream. An attempt compares its token against the
 * current one before applying anything, so a slow reconnect that resolves
 * after a newer one started is discarded instead of attaching to a thread the
 * reader has already left.
 */
export class Generation {
  #current = 0

  next(): number {
    this.#current += 1
    return this.#current
  }

  isCurrent(token: number): boolean {
    return token === this.#current
  }

  /** Invalidate every outstanding attempt without starting one. */
  invalidate(): void {
    this.#current += 1
  }
}

export interface RetryPlan {
  attempt: number
  delayMs: number
  final: boolean
}

/**
 * The schedule a caller should follow after a disconnect.
 *
 * Yielded rather than executed so the caller keeps control of cancellation and
 * of what happens between attempts — which for this application means asking
 * the server whether the run is still resumable before trying again.
 */
export function* retrySchedule(max: number = MAX_ATTEMPTS): Generator<RetryPlan> {
  for (let attempt = 1; attempt <= max; attempt += 1) {
    yield { attempt, delayMs: backoffFor(attempt), final: attempt === max }
  }
}

export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        // The caller's reason is passed through when it is an Error, so that a
        // stack survives. Anything else is replaced rather than thrown as-is:
        // rejecting with a bare string loses the stack and reads as a crash
        // with no origin wherever it is finally caught.
        const reason: unknown = signal.reason
        reject(
          reason instanceof Error
            ? reason
            : new DOMException('aborted', 'AbortError'),
        )
      },
      { once: true },
    )
  })
}
