/**
 * The HTTP client for ordinary requests.
 *
 * Streams do not go through here. They need a reader over the response body,
 * which means `fetch` — see `lib/stream/reader.ts`. Keeping the two separate
 * means neither has to carry the other's concerns: this file owns retries and
 * token refresh, and the stream reader owns framing and reconnection.
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

import { env } from '@/lib/env'

/** A failure shaped for the interface rather than for a log. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Machine-readable discriminator, when the server sent one. */
    readonly kind: string = 'unknown',
    /** Where the interface should send someone to fix it. */
    readonly action?: { url: string; label: string },
    /** Seconds to wait, when the server said. */
    readonly retryAfter?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  get retryable(): boolean {
    return this.status === 429 || this.status >= 500
  }
}

type TokenSource = () => Promise<string | null>
type RefreshHandler = () => Promise<string | null>

let tokenSource: TokenSource = async () => null
let refreshHandler: RefreshHandler = async () => null

/**
 * Supply the credentials the client should send.
 *
 * Injected rather than imported so this module has no opinion about which auth
 * provider a deployment uses, and so tests need no provider at all.
 */
export function configureAuth(options: {
  getToken: TokenSource
  refresh?: RefreshHandler
}): void {
  tokenSource = options.getToken
  if (options.refresh) refreshHandler = options.refresh
}

interface Retryable extends InternalAxiosRequestConfig {
  /**
   * Marks a request that has already been retried after a refresh.
   *
   * Without it, a token that is accepted for refresh but still rejected by the
   * API produces an unbounded loop of refresh-and-retry against a server that
   * is already saying no.
   */
  __retried?: boolean
}

function describe(error: AxiosError): ApiError {
  const status = error.response?.status ?? 0
  const body = error.response?.data

  if (!status) {
    return new ApiError(0, 'Could not reach the server.', 'network')
  }

  // The server sends `detail` as either a string or an object. Both shapes are
  // handled here so that no call site has to know which it got.
  const detail = (body as { detail?: unknown } | undefined)?.detail

  if (typeof detail === 'object' && detail !== null) {
    const structured = detail as {
      message?: string
      type?: string
      link?: { url: string; label: string }
      retry_after?: number
    }
    return new ApiError(
      status,
      structured.message ?? error.message,
      structured.type ?? 'unknown',
      structured.link,
      structured.retry_after,
    )
  }

  const retryAfterHeader = error.response?.headers?.['retry-after']
  const retryAfter = retryAfterHeader ? Number(retryAfterHeader) : undefined

  return new ApiError(
    status,
    typeof detail === 'string' ? detail : error.message,
    status === 429 ? 'rate_limited' : 'unknown',
    undefined,
    Number.isFinite(retryAfter) ? retryAfter : undefined,
  )
}

export function createClient(baseURL: string = env.apiBaseUrl): AxiosInstance {
  const client = axios.create({
    baseURL,
    timeout: 30_000,
    headers: { 'Content-Type': 'application/json' },
  })

  client.interceptors.request.use(async (config) => {
    // A replayed request already carries the freshly refreshed token. Asking
    // the token source again would hand back the stale one it still has
    // cached and overwrite the refresh, so the retry would fail exactly as the
    // original did — and the refresh would look broken rather than bypassed.
    if ((config as Retryable).__retried) return config

    const token = await tokenSource()
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  client.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
      const request = error.config as Retryable | undefined

      // One refresh, one replay. A second 401 means the credentials are wrong
      // rather than stale, and retrying again would only ask a server that has
      // already refused twice.
      if (error.response?.status === 401 && request && !request.__retried) {
        request.__retried = true
        const refreshed = await refreshHandler().catch(() => null)
        if (refreshed) {
          request.headers.Authorization = `Bearer ${refreshed}`
          return client.request(request)
        }
      }

      return Promise.reject(describe(error))
    },
  )

  return client
}

export const api = createClient()

/** Convenience wrappers, so call sites need not unwrap `.data` every time. */
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return (await api.get<T>(url, config)).data
}

export async function post<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return (await api.post<T>(url, body, config)).data
}

export async function patch<T>(url: string, body?: unknown): Promise<T> {
  return (await api.patch<T>(url, body)).data
}

export async function del(url: string): Promise<void> {
  await api.delete(url)
}
