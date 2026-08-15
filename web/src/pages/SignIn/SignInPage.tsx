/**
 * Signing in.
 *
 * Deliberately plain. The interesting decisions are about failure: the
 * service's own message is shown rather than a generic one, because "that
 * account does not exist" and "wrong password" call for different actions, and
 * only the first is worth a trip to the register form.
 */

import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/Button'
import { Surface } from '@/components/ui/Surface'
import { register, signIn, SignInFailed } from '@/lib/auth'
import { cn } from '@/lib/cn'

type Mode = 'sign-in' | 'register'

export function SignInPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState<Mode>('sign-in')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Where the guard sent them from. Going back there rather than to a default
  // page is the difference between signing in and losing your place.
  const intended =
    (location.state as { from?: string } | null)?.from ?? '/threads'

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)

    try {
      if (mode === 'register') {
        await register(username, password)
        // Registering does not sign you in — the service mints a token only on
        // login. Doing it here keeps that from being a second form to fill in.
        await signIn(username, password)
      } else {
        await signIn(username, password)
      }
      navigate(intended, { replace: true })
    } catch (failure) {
      setError(
        failure instanceof SignInFailed
          ? failure.message
          : 'Something went wrong signing in.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <Surface elevation="raised" padding="lg" className="w-full max-w-sm">
        <h1 className="text-lg font-semibold tracking-tight text-ink">
          {mode === 'sign-in' ? 'Sign in' : 'Create an account'}
        </h1>
        <p className="mt-1 text-sm text-ink-muted">Kairos Trader</p>

        <form onSubmit={(e) => void submit(e)} className="mt-6 space-y-4">
          <Field
            id="username"
            label="Username"
            value={username}
            onChange={setUsername}
            autoComplete="username"
          />
          <Field
            id="password"
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
          />

          {error !== null && (
            <p role="alert" className="text-sm text-negative">
              {error}
            </p>
          )}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={busy}
            disabled={!username || !password}
          >
            {mode === 'sign-in' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <button
          type="button"
          className="mt-4 text-sm text-accent underline underline-offset-2"
          onClick={() => {
            setMode(mode === 'sign-in' ? 'register' : 'sign-in')
            setError(null)
          }}
        >
          {mode === 'sign-in' ? 'Create an account' : 'I already have an account'}
        </button>
      </Surface>
    </div>
  )
}

function Field({
  id,
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  autoComplete?: string
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        autoComplete={autoComplete}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          'mt-1 h-9 w-full rounded border border-border bg-surface px-2 text-sm text-ink',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        )}
      />
    </div>
  )
}
