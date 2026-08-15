import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],

  // Matched to the attribute the theme provider actually sets. The reference
  // implementation declared `["class"]` while writing `data-theme`, so every
  // `dark:` utility in the codebase compiled to a selector that never matched
  // — the dark theme worked only because the CSS variables happened to carry
  // it, and any utility relying on the variant was silently dead.
  darkMode: ['selector', '[data-theme="dark"]'],

  theme: {
    extend: {
      // Every colour resolves through a token, so a component never names a
      // literal. Changing the palette is then a change to one file rather than
      // a search across several hundred.
      colors: {
        canvas: 'var(--canvas)',
        surface: 'var(--surface)',
        raised: 'var(--raised)',
        border: 'var(--border)',
        ink: {
          DEFAULT: 'var(--ink)',
          muted: 'var(--ink-muted)',
          faint: 'var(--ink-faint)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          soft: 'var(--accent-soft)',
          ink: 'var(--accent-ink)',
        },
        positive: 'var(--positive)',
        negative: 'var(--negative)',
        caution: 'var(--caution)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        DEFAULT: 'var(--radius)',
        lg: 'var(--radius-lg)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      // Named so that a component says what a layer is for rather than how
      // high it sits — and so the ordering lives in one place.
      zIndex: {
        raised: '10',
        sticky: '20',
        overlay: '30',
        modal: '40',
        popover: '50',
        toast: '60',
      },
    },
  },

  plugins: [],
} satisfies Config
