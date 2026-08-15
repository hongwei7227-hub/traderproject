import { useCallback, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'

import { Composer } from '@/components/chat/Composer'
import { TurnView } from '@/components/chat/TurnView'
import { useThread, useRefreshAfterTurn } from '@/hooks/useThreads'
import { useTurnStream } from '@/hooks/useTurnStream'
import { isSettled } from '@/lib/turn/state'

/**
 * One conversation.
 *
 * The page owns very little: the stream hook holds the turn, the query hook
 * holds the thread, and this arranges them. Anything it did hold would be a
 * second copy of state that already exists somewhere authoritative.
 */
export function ChatPage() {
  const { threadId = '' } = useParams<{ threadId: string }>()
  const thread = useThread(threadId || null)
  const refreshAfterTurn = useRefreshAfterTurn()

  const onSettled = useCallback(() => {
    // A finished turn changes the thread's title and timestamp server-side.
    // Without this the list keeps what it fetched before the conversation.
    if (threadId) refreshAfterTurn(threadId)
  }, [threadId, refreshAfterTurn])

  const { turn, connection, send, stop } = useTurnStream({ threadId, onSettled })

  const busy = connection === 'streaming' || connection === 'reconnecting'

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-4 p-4">
      <header className="flex items-baseline justify-between gap-4">
        <h1 className="truncate text-lg font-medium text-ink">
          {thread.data?.title ?? 'New conversation'}
        </h1>
        {connection === 'reconnecting' && (
          <span className="shrink-0 text-xs text-caution" role="status">
            Reconnecting…
          </span>
        )}
      </header>

      <Transcript>
        {turn.phase === 'idle' ? (
          <Empty />
        ) : (
          <TurnView turn={turn} />
        )}
      </Transcript>

      <Composer
        onSend={(prompt) => void send({ prompt })}
        onStop={stop}
        busy={busy}
        disabled={threadId.length === 0}
      />
    </div>
  )
}

/**
 * The scrolling region.
 *
 * Follows new content only while the reader is already at the bottom. Scrolling
 * unconditionally yanks the view away from someone who scrolled up to read
 * something earlier — which during a long answer is most of the time.
 */
function Transcript({ children }: { children: React.ReactNode }) {
  const region = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)

  const onScroll = useCallback(() => {
    const element = region.current
    if (!element) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    // A few pixels of tolerance: sub-pixel layout means an element at the
    // bottom rarely reports exactly zero.
    pinned.current = distance < 24
  }, [])

  useEffect(() => {
    if (pinned.current) {
      region.current?.scrollTo({ top: region.current.scrollHeight })
    }
  })

  return (
    <div
      ref={region}
      onScroll={onScroll}
      className="flex-1 overflow-y-auto rounded-lg border border-border bg-surface p-4"
    >
      {children}
    </div>
  )
}

function Empty() {
  return (
    <p className="pt-8 text-center text-sm text-ink-faint">
      Ask a question to begin.
    </p>
  )
}
