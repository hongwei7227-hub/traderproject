/**
 * Running a turn from a component.
 *
 * Owns three things a component should not have to: the connection, the
 * cursor, and the question of which attempt is still wanted. What it exposes
 * is a piece of state that folds forward as events arrive, and two verbs.
 *
 * The rule that shapes the rest: exactly one stream belongs to this hook at a
 * time, and abandoning one must cancel it. A turn left running into a
 * connection nobody reads costs the same as one somebody reads.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { env } from '@/lib/env'
import { readStream, type StreamEvent } from '@/lib/stream/reader'
import {
  delay,
  Generation,
  reconnect,
  retrySchedule,
  StreamCursor,
} from '@/lib/stream/reconnect'
import { emptyTurn, reduceTurn, type TurnState } from '@/lib/turn/state'
import { asTurnEvent, type SendMessageBody } from '@/types/api'

export type Connection = 'idle' | 'streaming' | 'reconnecting' | 'closed'

export interface UseTurnStream {
  turn: TurnState
  connection: Connection
  send: (body: SendMessageBody) => Promise<void>
  stop: () => void
}

interface Options {
  threadId: string
  /** Called once a turn settles, so a caller can refresh what it persisted. */
  onSettled?: (turn: TurnState) => void
  getHeaders?: () => Promise<Record<string, string>>
}

export function useTurnStream({
  threadId,
  onSettled,
  getHeaders,
}: Options): UseTurnStream {
  const [turn, setTurn] = useState<TurnState>(emptyTurn)
  const [connection, setConnection] = useState<Connection>('idle')

  const cursor = useRef(new StreamCursor())
  const generation = useRef(new Generation())
  const abort = useRef<AbortController | null>(null)
  // Read in the settle callback, which must see the final fold rather than
  // the value captured when the stream started.
  const latest = useRef<TurnState>(turn)

  const apply = useCallback((event: StreamEvent) => {
    const typed = asTurnEvent(event)
    if (!typed) return // a kind this build predates; ignoring beats breaking
    setTurn((previous) => {
      const folded = reduceTurn(previous, typed)
      latest.current = folded
      return folded
    })
  }, [])

  const stop = useCallback(() => {
    // Invalidate first: an in-flight attempt that resolves after this must not
    // reattach, and cancelling alone does not stop one already past its await.
    generation.current.invalidate()
    abort.current?.abort()
    abort.current = null
    setConnection('closed')
  }, [])

  // A component unmounting is a reader walking away. Without this the turn
  // keeps running, keeps spending, and writes into a connection nobody holds.
  useEffect(() => stop, [stop])

  const resume = useCallback(
    async (token: number) => {
      for (const plan of retrySchedule()) {
        if (!generation.current.isCurrent(token)) return

        setConnection('reconnecting')
        try {
          await delay(plan.delayMs, abort.current?.signal)
        } catch {
          return // aborted while waiting
        }

        if (!generation.current.isCurrent(token)) return

        const outcome = await reconnect({
          threadId,
          cursor: cursor.current,
          onEvent: apply,
          headers: (await getHeaders?.()) ?? {},
          ...(abort.current ? { signal: abort.current.signal } : {}),
        })

        if (outcome.aborted || !generation.current.isCurrent(token)) return
        if (!outcome.disconnected) {
          setConnection('closed')
          onSettled?.(latest.current)
          return
        }
      }

      // The schedule ran out. The turn may well have finished server-side, so
      // the caller refetches rather than showing a half-answer as final.
      setConnection('closed')
      onSettled?.(latest.current)
    },
    [threadId, apply, getHeaders, onSettled],
  )

  const send = useCallback(
    async (body: SendMessageBody) => {
      stop()

      const token = generation.current.next()
      const controller = new AbortController()
      abort.current = controller

      cursor.current = new StreamCursor()
      const start = emptyTurn()
      latest.current = start
      setTurn(start)
      setConnection('streaming')

      const outcome = await readStream(
        `${env.apiBaseUrl}/api/v1/threads/${threadId}/messages`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...((await getHeaders?.()) ?? {}),
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        },
        {
          onEvent: (event) => {
            cursor.current.observe(event)
            apply(event)
          },
          // Latched from the response headers, which arrive before any frame —
          // so a drop before the first event still knows what to resume.
          onHeaders: (location) => cursor.current.latchFromContentLocation(location),
        },
      )

      if (!generation.current.isCurrent(token)) return

      if (outcome.aborted) {
        setConnection('closed')
        return
      }

      if (outcome.disconnected) {
        await resume(token)
        return
      }

      setConnection('closed')
      onSettled?.(latest.current)
    },
    [threadId, apply, getHeaders, onSettled, resume, stop],
  )

  return { turn, connection, send, stop }
}
