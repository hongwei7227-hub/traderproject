import { MessageSquare, Moon, Sun, Monitor } from 'lucide-react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { Button } from '@/components/ui/Button'
import { useTheme, type ThemeChoice } from '@/contexts/ThemeContext'
import { cn } from '@/lib/cn'
import { ChatPage } from '@/pages/Chat/ChatPage'
import { ThreadListPage } from '@/pages/Threads/ThreadListPage'

export function App() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/threads" replace />} />
          <Route path="/threads" element={<ThreadListPage />} />
          <Route path="/threads/:threadId" element={<ChatPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

function Sidebar() {
  return (
    <nav
      className="flex w-56 shrink-0 flex-col border-r border-border bg-surface"
      aria-label="Main"
    >
      <div className="px-4 py-4">
        <span className="text-sm font-semibold tracking-tight text-ink">Kairos</span>
      </div>

      <ul className="flex-1 px-2">
        <li>
          <NavLink
            to="/threads"
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2 rounded px-3 py-2 text-sm transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                isActive
                  ? 'bg-accent-soft text-ink'
                  : 'text-ink-muted hover:bg-raised hover:text-ink',
              )
            }
          >
            <MessageSquare className="h-4 w-4" aria-hidden="true" />
            Conversations
          </NavLink>
        </li>
      </ul>

      <div className="border-t border-border p-2">
        <ThemeToggle />
      </div>
    </nav>
  )
}

const THEMES: ReadonlyArray<{ value: ThemeChoice; label: string; Icon: typeof Sun }> = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
]

/**
 * Three buttons rather than a toggle.
 *
 * A two-state toggle cannot express "follow the system", so choosing it once
 * means never getting it back without clearing storage — and following the
 * system is the state most people want.
 */
function ThemeToggle() {
  const { choice, setChoice } = useTheme()

  return (
    <div
      className="flex gap-1 rounded border border-border p-1"
      role="radiogroup"
      aria-label="Theme"
    >
      {THEMES.map(({ value, label, Icon }) => (
        <Button
          key={value}
          variant="ghost"
          size="icon"
          role="radio"
          aria-checked={choice === value}
          aria-label={label}
          onClick={() => setChoice(value)}
          className={cn('h-7 flex-1', choice === value && 'bg-raised text-ink')}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      ))}
    </div>
  )
}

function NotFound() {
  return (
    <div className="p-8 text-center">
      <p className="text-sm text-ink-muted">That page does not exist.</p>
    </div>
  )
}
