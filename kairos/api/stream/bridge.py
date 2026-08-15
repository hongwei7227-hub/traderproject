"""Carrying events from a running turn to an open connection.

The engine produces events as it goes; the response consumes them as they
arrive. Between the two sits a queue, because the alternative — collecting the
turn's events and rendering them when it finishes — is not streaming at all. A
turn that takes ninety seconds would show nothing for ninety seconds and then
everything, which is the behaviour the whole protocol exists to avoid.

The queue is bounded. An unbounded one turns a client that stopped reading into
unbounded memory growth on the server, and a turn producing tokens faster than
a phone on a train can accept them is ordinary rather than exceptional.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from typing import Final

from kairos.core.streaming.events import Event, EventKind, EventStream

# One sentinel instance, compared by identity: an event that happened to equal
# it would otherwise end the stream early.
_END: Final = object()

# Deep enough to absorb a burst of token events, shallow enough that a reader
# which has stopped reading is noticed rather than accumulated.
DEFAULT_CAPACITY: Final = 256


class StreamBridge:
    """A live stream, readable as an async iterator.

    Wraps an `EventStream` so that everything the engine emits is both recorded
    (for the transcript) and forwarded (to the connection).
    """

    __slots__ = ("_stream", "_queue", "_closed", "_dropped")

    def __init__(self, stream: EventStream, capacity: int = DEFAULT_CAPACITY) -> None:
        self._stream = stream
        self._queue: asyncio.Queue[Event | object] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self._dropped = 0

    @property
    def stream(self) -> EventStream:
        return self._stream

    @property
    def dropped(self) -> int:
        """Events discarded because the reader could not keep up."""
        return self._dropped

    def publish(self, event: Event) -> None:
        """Offer an event to the reader, without waiting for it.

        Called from the engine's own task, which must not block on a slow
        connection: one reader on a bad network would otherwise stall the turn
        it is watching, and with it the tools and model calls behind it.

        When the queue is full, incremental text is dropped and structural
        events are not. Losing a token chunk degrades the reading experience;
        losing a terminal event leaves the client waiting forever on a
        connection nothing will write to again.
        """
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if event.terminal or event.kind is EventKind.METADATA:
                # Structural. Make room by discarding the oldest content event
                # rather than dropping this one.
                self._evict_oldest()
                self._queue.put_nowait(event)
            else:
                self._dropped += 1

    def _evict_oldest(self) -> None:
        try:
            self._queue.get_nowait()
            self._queue.task_done()
        except asyncio.QueueEmpty:  # pragma: no cover — full implies non-empty
            pass

    def close(self) -> None:
        """Signal that no further events will arrive."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_END)
        except asyncio.QueueFull:
            self._evict_oldest()
            self._queue.put_nowait(_END)

    async def events(self) -> AsyncIterator[Event]:
        """Yield events as they arrive, ending when the producer closes."""
        while True:
            item = await self._queue.get()
            if item is _END:
                return
            assert isinstance(item, Event)
            yield item


async def run_and_stream(
    bridge: StreamBridge, work: Awaitable[object]
) -> AsyncIterator[Event]:
    """Run a turn and yield its events as they happen.

    The turn runs as its own task so that producing and consuming overlap. If
    the client disconnects the iterator is closed, and the `finally` cancels
    the turn rather than leaving it to finish into a connection nobody is
    reading — which is where the reference implementation leaked both sandbox
    time and model spend.
    """
    task = asyncio.ensure_future(work)
    task.add_done_callback(lambda _: bridge.close())

    try:
        async for event in bridge.events():
            yield event
    finally:
        if not task.done():
            task.cancel()
        # Await the cancellation so the turn's own cleanup — settling quota,
        # marking the row terminal — runs before this request ends.
        with _suppress_cancelled():
            await task


class _suppress_cancelled:
    """Context manager swallowing exactly CancelledError."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)


class RecordingStream(EventStream):
    """An `EventStream` that also forwards to a bridge.

    Subclassed rather than wrapped so that the engine, which knows only about
    `EventStream`, needs no awareness that anything is watching.
    """

    __slots__ = ("_bridge",)

    def __init__(self, run_id, bridge: StreamBridge | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(run_id)
        self._bridge = bridge

    def attach(self, bridge: StreamBridge) -> None:
        self._bridge = bridge

    def _emit(self, kind, payload):  # type: ignore[no-untyped-def]
        event = super()._emit(kind, payload)
        if self._bridge is not None:
            self._bridge.publish(event)
        return event
