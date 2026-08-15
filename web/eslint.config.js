/**
 * Lint rules, kept to the ones that catch real defects.
 *
 * Formatting is not linted. Two tools disagreeing about where a brace goes
 * produces noise that trains people to run `--fix` without reading, which is
 * exactly the habit that lets a genuine finding through.
 *
 * The rules that are on are the ones with a failure mode: a floating promise
 * that swallows an error, a hook dependency that goes stale, a `switch` over a
 * union that quietly stops being exhaustive.
 */

import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },

  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        // Type-aware rules need the program, not just the syntax tree. Without
        // it the interesting half of the rule set silently does nothing.
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // A promise nobody awaits is a rejection nobody handles. `void` is the
      // way to say the omission is deliberate, and it reads as such.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',

      // The rewrite exists partly to remove assertion escapes; leaving the
      // rule off would let them back in one call site at a time.
      '@typescript-eslint/no-unsafe-assignment': 'error',
      '@typescript-eslint/no-unsafe-member-access': 'error',
      '@typescript-eslint/no-unsafe-return': 'error',

      // Off. A function is async here because its signature says it returns a
      // promise — an injected token source, a test double standing in for one.
      // Demanding an `await` inside would mean either adding a meaningless one
      // or dropping `async` and hand-wrapping the return.
      '@typescript-eslint/require-await': 'off',

      // An unused parameter named with a leading underscore is a documented
      // signature, not an oversight.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },

  {
    // Config files run in Node and are not part of the app's program.
    files: ['*.config.{ts,js}'],
    languageOptions: { globals: { ...globals.node } },
    ...tseslint.configs.disableTypeChecked,
  },

  {
    files: ['src/**/__tests__/**', 'src/test/**'],
    languageOptions: { globals: { ...globals.node } },
    rules: {
      // Tests construct malformed input on purpose — that is what they are
      // checking the handling of.
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
