import {
  CandlestickChart,
  CreditCard,
  MessageSquare,
  Monitor,
  Moon,
  SlidersHorizontal,
  Sun,
} from 'lucide-react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactElement } from 'react'

import { Button } from '@/components/ui/Button'
import { useSession } from '@/contexts/SessionContext'
import { useTheme, type ThemeChoice } from '@/contexts/ThemeContext'
import { cn } from '@/lib/cn'
import { isSoloMode } from '@/lib/env'
import { BillingPage } from '@/pages/Billing/BillingPage'
import { ChatPage } from '@/pages/Chat/ChatPage'
import { SettingsPage } from '@/pages/Settings/SettingsPage'
import { SignInPage } from '@/pages/SignIn/SignInPage'
import { ThreadListPage } from '@/pages/Threads/ThreadListPage'
import { TradingPage } from '@/pages/Trading/TradingPage'

export function App() {
  return (
    <Routes>
      {/* Outside the shell: it is the page someone lands on when they have no
          session, so it cannot be behind the thing that requires one. */}
      <Route path="/signin" element={<SignInPage />} />
      <Route
        path="*"
        element={
          <RequireSession>
            <Shell />
          </RequireSession>
        }
      />
    </Routes>
  )
}

/**
 * The guard.
 *
 * Remembers where someone was headed and sends them back there after signing
 * in. Without that, being bounced to the login page means losing your place as
 * well as your session.
 */
function RequireSession({ children }: { children: ReactElement }) {
  const { signedIn } = useSession()
  const location = useLocation()

  if (!signedIn) {
    return <Navigate to="/signin" replace state={{ from: location.pathname }} />
  }
  return children
}

function Shell() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/threads" replace />} />
          <Route path="/threads" element={<ThreadListPage />} />
          <Route path="/threads/:threadId" element={<ChatPage />} />
          <Route path="/trading" element={<TradingPage />} />
          <Route path="/billing" element={<BillingPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

/**
 * The navigation, as data.
 *
 * One source for the sidebar so that adding a destination is adding an entry
 * rather than editing markup in more than one place — which is how a route
 * comes to exist with no way to reach it.
 */
const DESTINATIONS = [
  { to: '/threads', label: 'Conversations', Icon: MessageSquare },
  { to: '/trading', label: 'Trading', Icon: CandlestickChart },
  { to: '/billing', label: 'Membership', Icon: CreditCard },
  { to: '/settings', label: 'Settings', Icon: SlidersHorizontal },
] as const

function Sidebar() {
  const { session, signOut } = useSession()

  return (
    <nav
      className="flex w-56 shrink-0 flex-col border-r border-border bg-surface"
      aria-label="Main"
    >
      <div className="px-4 py-4">
        <span className="text-sm font-semibold tracking-tight text-ink">
          Kairos Trader
        </span>
      </div>

      <ul className="flex-1 space-y-0.5 px-2">
        {DESTINATIONS.map(({ to, label, Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
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
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="space-y-2 border-t border-border p-2">
        {/* A single-user build has nobody to sign out as, so offering it would
            be a control that does nothing. */}
        {!isSoloMode && session && (
          <div className="flex items-center justify-between px-1">
            <span className="truncate text-xs text-ink-muted" title={session.username}>
              {session.username}
            </span>
            <Button variant="ghost" size="sm" onClick={signOut}>
              Sign out
            </Button>
          </div>
        )}
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
