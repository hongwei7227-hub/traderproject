/**
 * Framing, and the failure modes a hand-written reader has to get right.
 */

import { describe, expect, it, vi } from 'vitest'

import { readStream, StreamHttpError, type StreamEvent } from '../reader'

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

function respondWith(
  body: ReadableStream<Uint8Array> | null,
  init: { status?: number; headers?: Record<string, string> } = {},
): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(body, {
        status: init.status ?? 200,
        headers: { 'content-type': 'text/event-stream', ...init.headers },
      }),
    ),
  )
}

function collect(): { events: StreamEvent[]; onEvent: (e: StreamEvent) => void } {
  const events: StreamEvent[] = []
  return { events, onEvent: (e) => events.push(e) }
}

describe('framing', () => {
  it('parses a complete frame', async () => {
    respondWith(streamOf('id: 0\nevent: text\ndata: {"text":"hi"}\n\n'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events).toHaveLength(1)
    expect(sink.events[0]).toMatchObject({ kind: 'text', id: 0 })
    expect(sink.events[0]?.data).toEqual({ text: 'hi' })
  })

  it('reassembles a frame split across chunks', async () => {
    // The network decides where chunks end; a reader that assumes they align
    // with frames drops the ones that straddle a boundary.
    respondWith(streamOf('id: 0\nevent: te', 'xt\ndata: {"text":"hi"}', '\n\n'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events[0]?.data).toEqual({ text: 'hi' })
  })

  it('delivers several frames from one chunk', async () => {
    respondWith(
      streamOf('event: a\ndata: {}\n\nevent: b\ndata: {}\n\nevent: c\ndata: {}\n\n'),
    )
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events.map((e) => e.kind)).toEqual(['a', 'b', 'c'])
  })

  it('ignores comment lines', async () => {
    // Keepalives are comments precisely so a client needs no special case.
    respondWith(streamOf(': keepalive\n\nevent: text\ndata: {"t":1}\n\n'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events).toHaveLength(1)
  })

  it('drops a malformed payload without ending the stream', async () => {
    // One bad frame must not abandon an answer that is otherwise arriving.
    respondWith(streamOf('event: bad\ndata: {not json\n\nevent: ok\ndata: {}\n\n'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events.map((e) => e.kind)).toEqual(['ok'])
  })

  it('leaves a partial trailing frame undelivered', async () => {
    respondWith(streamOf('event: whole\ndata: {}\n\nevent: partial\ndata: {'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events.map((e) => e.kind)).toEqual(['whole'])
  })

  it('handles multi-line data fields', async () => {
    respondWith(streamOf('event: t\ndata: {"a":\ndata: 1}\n\n'))
    const sink = collect()

    await readStream('/s', {}, sink)

    expect(sink.events[0]?.data).toEqual({ a: 1 })
  })
})

describe('headers', () => {
  it('reports the reconnect location before any event', async () => {
    // This is what closes the reconnect race: a client dropping between the
    // request and the first frame still knows which run to resume.
    const order: string[] = []
    respondWith(streamOf('event: metadata\ndata: {}\n\n'), {
      headers: { 'content-location': '/api/v1/threads/t/messages/stream?run_id=r-1' },
    })

    await readStream(
      '/s',
      {},
      {
        onEvent: () => order.push('event'),
        onHeaders: () => order.push('headers'),
      },
    )

    expect(order).toEqual(['headers', 'event'])
  })

  it('captures the location even on an error response', async () => {
    respondWith(null, {
      status: 500,
      headers: { 'content-location': '/resume?run_id=r-9' },
    })
    let seen: string | null = null

    await expect(
      readStream('/s', {}, { onEvent: () => {}, onHeaders: (l) => (seen = l) }),
    ).rejects.toBeInstanceOf(StreamHttpError)

    expect(seen).toContain('run_id=r-9')
  })
})

describe('outcomes', () => {
  it('reports a clean end', async () => {
    respondWith(streamOf('event: done\ndata: {}\n\n'))

    const outcome = await readStream('/s', {}, collect())

    expect(outcome).toMatchObject({ disconnected: false, aborted: false })
  })

  it('reports the highest event id seen', async () => {
    respondWith(streamOf('id: 3\nevent: a\ndata: {}\n\nid: 7\nevent: b\ndata: {}\n\n'))

    const outcome = await readStream('/s', {}, collect())

    expect(outcome.lastEventId).toBe(7)
  })

  it('treats a failed request as a disconnect, not an error', async () => {
    // The caller already has a reconnect path; making this throw would force
    // it to special-case something it already handles.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down')
      }),
    )

    const outcome = await readStream('/s', {}, collect())

    expect(outcome).toMatchObject({ disconnected: true, aborted: false })
  })

  it('distinguishes a deliberate abort from a crash', async () => {
    // An abort raises a DOMException, which is not an Error in every runtime,
    // so `instanceof Error` misclassifies a user pressing stop as a failure.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new DOMException('aborted', 'AbortError')
      }),
    )

    const outcome = await readStream('/s', {}, collect())

    expect(outcome).toMatchObject({ aborted: true, disconnected: false })
  })

  it('raises on an HTTP error so the caller can distinguish it', async () => {
    respondWith(null, { status: 403 })

    await expect(readStream('/s', {}, collect())).rejects.toMatchObject({
      status: 403,
    })
  })
})
