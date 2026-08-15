/**
 * Theme selection.
 *
 * Three states, not two: light, dark, and no choice at all. The third is the
 * default and follows the system, which means someone who has never opened
 * settings gets what their machine asked for — and someone who has chosen
 * keeps their choice when the machine changes its mind at sunset.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'kairos.theme'

interface ThemeValue {
  choice: ThemeChoice
  resolved: ResolvedTheme
  setChoice: (choice: ThemeChoice) => void
}

const ThemeContext = createContext<ThemeValue | null>(null)

function readStoredChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    // Storage can throw in a private window or with cookies disabled.
    // Following the system is a fine answer when the preference is unreadable.
    return 'system'
  }
}

function systemPrefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readStoredChoice)
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  const resolved: ResolvedTheme =
    choice === 'system' ? (systemDark ? 'dark' : 'light') : choice

  // Applied before paint. An effect that runs after would show one frame of
  // the wrong theme on every load, which reads as a flash rather than as a
  // preference being honoured.
  useLayoutEffect(() => {
    const root = document.documentElement
    root.dataset['theme'] = resolved
    root.style.colorScheme = resolved
  }, [resolved])

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    try {
      if (next === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // The choice still applies for this session; it just will not survive a
      // reload. Better than failing the interaction.
    }
  }, [])

  const value = useMemo(
    () => ({ choice, resolved, setChoice }),
    [choice, resolved, setChoice],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext)
  if (!value) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return value
}
