/// <reference types="vite/client" />

/**
 * Build-time variables, declared so a typo is a compile error.
 *
 * Vite's own type for `import.meta.env` allows any string key, which makes
 * `VITE_HSOT_MODE` type-check and read as undefined at runtime. Naming them
 * here turns that into the build failure it should be.
 */
interface ImportMetaEnv {
  readonly VITE_HOST_MODE?: 'oss' | 'platform'
  readonly VITE_API_BASE_URL?: string
  readonly VITE_PROXY_BACKEND?: string
  readonly VITE_AUTH_USER_ID?: string
  readonly VITE_SUPABASE_URL?: string
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string
  readonly VITE_CDN_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
