/**
 * Reading a server-sent event stream.
 *
 * Not `EventSource`, for three reasons this application hits all of:
 *
 *   1. It only issues GET, but sending a message is a POST whose body carries
 *      the prompt and whose response is the stream.
 *   2. It cannot set headers, so a bearer token would have to travel in the
 *      query string — where it lands in every access log along the way.
 *   3. Its reconnect policy is fixed, and this needs a precise cursor plus
 *      backoff plus a guard against a stale attempt resuming after a newer one
 *      has taken over.
 *
 * So: `fetch`, a reader over the body, and manual framing.
 */

export interface StreamEvent {
  /** The `event:` field. */
  readonly kind: string
  /** The `id:` field, as a number when it parses as one. */
  readonly id: number | null
  /** The parsed `data:` payload. */
  readonly data: Record<string, unknown>
}

export interface StreamOutcome {
  /** The connection dropped rather than ending cleanly. */
  readonly disconnected: boolean
  /** The caller aborted it. */
  readonly aborted: boolean
  /** Where to reconnect, captured from the response headers. */
  readonly contentLocation: string | null
  /** The highest event id seen on the main trunk. */
  readonly lastEventId: number | null
}

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void
  /**
   * Called as soon as response headers arrive, before any body byte.
   *
   * This is what closes the reconnect race. The run id is carried in the
   * `Content-Location` header, so a client that drops between the request and
   * the first `metadata` frame still knows which run to resume — whereas
   * waiting for that frame leaves a window in which a disconnect strands it.
   */
  onHeaders?: (contentLocation: string | null) => void
}

export class StreamHttpError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`stream request failed with ${status}`)
    this.name = 'StreamHttpError'
  }
}

/**
 * True for the abort a caller triggered.
 *
 * Matched by name rather than `instanceof`: an abort raises a `DOMException`,
 * which is not an `Error` in every runtime, so an `instanceof Error` check
 * silently misclassifies a deliberate stop as a crash.
 */
function isAbort(cause: unknown): boolean {
  return (
    typeof cause === 'object' &&
    cause !== null &&
    'name' in cause &&
    (cause as { name?: unknown }).name === 'AbortError'
  )
}

function parseFrame(block: string): StreamEvent | null {
  let kind = 'message'
  let id: number | null = null
  const dataLines: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith(':')) continue // comment, e.g. a keepalive
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    const value = separator === -1 ? '' : line.slice(separator + 1).replace(/^ /, '')

    if (field === 'event') kind = value
    else if (field === 'data') dataLines.push(value)
    else if (field === 'id') {
      const parsed = Number(value)
      id = Number.isFinite(parsed) ? parsed : null
    }
  }

  if (dataLines.length === 0) return null

  try {
    const parsed: unknown = JSON.parse(dataLines.join('\n'))
    const data =
      typeof parsed === 'object' && parsed !== null
        ? (parsed as Record<string, unknown>)
        : { value: parsed }
    return { kind, id, data }
  } catch {
    // A malformed frame is dropped rather than fatal. One bad payload should
    // not end a stream that is otherwise delivering an answer.
    return null
  }
}

/**
 * Read a stream to completion, dispatching each event.
 *
 * Resolves rather than rejects on a dropped connection: a disconnect is an
 * expected outcome that the caller handles by reconnecting, not an error.
 */
export async function readStream(
  url: string,
  init: RequestInit,
  handlers: StreamHandlers,
): Promise<StreamOutcome> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch (cause) {
    if (isAbort(cause)) {
      return { disconnected: false, aborted: true, contentLocation: null, lastEventId: null }
    }
    // The request never reached the server. Reported as a disconnect so the
    // caller's reconnect path handles it, rather than as an error it would
    // have to special-case.
    return { disconnected: true, aborted: false, contentLocation: null, lastEventId: null }
  }

  // Snapshotted before reading the body, so it survives an error response.
  const contentLocation = response.headers.get('content-location')
  handlers.onHeaders?.(contentLocation)

  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new StreamHttpError(response.status, detail.slice(0, 500))
  }

  if (!response.body) {
    return { disconnected: true, aborted: false, contentLocation, lastEventId: null }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventId: number | null = null

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line. Anything after the last
      // separator is a partial frame and stays in the buffer.
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)

        const event = parseFrame(block)
        if (event) {
          if (event.id !== null) lastEventId = event.id
          handlers.onEvent(event)
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
  } catch (cause) {
    if (isAbort(cause)) {
      return { disconnected: false, aborted: true, contentLocation, lastEventId }
    }
    return { disconnected: true, aborted: false, contentLocation, lastEventId }
  } finally {
    reader.releaseLock()
  }

  return { disconnected: false, aborted: false, contentLocation, lastEventId }
}
