import { MessageSquare, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/Button'
import { Surface } from '@/components/ui/Surface'
import { useDeleteThread, useThreads } from '@/hooks/useThreads'

export function ThreadListPage() {
  const threads = useThreads()
  const remove = useDeleteThread()

  if (threads.isPending) return <Skeleton />

  if (threads.isError) {
    return (
      <Surface className="m-4" elevation="flat">
        <p className="text-sm text-ink">Could not load conversations.</p>
        <Button className="mt-3" onClick={() => void threads.refetch()}>
          Try again
        </Button>
      </Surface>
    )
  }

  const items = threads.data?.threads ?? []

  if (items.length === 0) {
    return (
      <div className="p-8 text-center">
        <MessageSquare
          className="mx-auto h-8 w-8 text-ink-faint"
          aria-hidden="true"
        />
        <p className="mt-3 text-sm text-ink-muted">No conversations yet.</p>
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-2 p-4">
      {items.map((thread) => (
        <li key={thread.id}>
          <Surface
            padding="none"
            className="flex items-center gap-2 pr-2 transition-colors hover:bg-raised"
          >
            <Link
              to={`/threads/${thread.id}`}
              className="min-w-0 flex-1 px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <p className="truncate text-sm font-medium text-ink">
                {thread.title ?? 'Untitled'}
              </p>
              <p className="mt-0.5 text-xs text-ink-faint">
                {new Date(thread.updated_at).toLocaleString()}
              </p>
            </Link>

            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete ${thread.title ?? 'conversation'}`}
              loading={remove.isPending && remove.variables === thread.id}
              onClick={() => remove.mutate(thread.id)}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </Button>
          </Surface>
        </li>
      ))}
    </ul>
  )
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-2 p-4" aria-busy="true" aria-label="Loading">
      {[0, 1, 2].map((index) => (
        <div key={index} className="h-16 animate-pulse rounded-lg bg-raised" />
      ))}
    </div>
  )
}
