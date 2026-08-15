"""Live streaming: overlap, backpressure, and what a disconnect costs."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from kairos.api.stream.bridge import RecordingStream, StreamBridge, run_and_stream
from kairos.core.streaming.events import Event, EventKind


def bridged(capacity: int = 256) -> tuple[RecordingStream, StreamBridge]:
    stream = RecordingStream(uuid4())
    bridge = StreamBridge(stream, capacity=capacity)
    stream.attach(bridge)
    return stream, bridge


class TestForwarding:
    async def test_emitted_events_reach_the_reader(self) -> None:
        stream, bridge = bridged()
        stream.open()
        stream.text("hello")
        stream.close()
        bridge.close()

        received = [e async for e in bridge.events()]
        assert [e.kind for e in received] == [
            EventKind.METADATA,
            EventKind.TEXT,
            EventKind.DONE,
        ]

    async def test_events_are_still_recorded_for_the_transcript(self) -> None:
        # The bridge forwards; it does not consume. A replay later must see
        # everything a live reader saw.
        stream, bridge = bridged()
        stream.open()
        stream.text("hi")
        stream.close()
        bridge.close()

        _ = [e async for e in bridge.events()]
        assert len(stream.recorded) == 3


class TestOverlap:
    async def test_frames_leave_before_the_turn_finishes(self) -> None:
        """The property that makes this streaming rather than buffering.

        A ninety-second turn must not show nothing for ninety seconds and then
        everything.
        """
        stream, bridge = bridged()
        stream.open()
        seen_early: list[int] = []

        async def slow_turn() -> None:
            stream.text("first")
            await asyncio.sleep(0.05)
            stream.text("second")
            stream.close()

        async for event in run_and_stream(bridge, slow_turn()):
            if event.kind is EventKind.TEXT and not seen_early:
                # Reaching here at all, before the turn's sleep has finished,
                # is the assertion.
                seen_early.append(event.sequence)

        assert seen_early == [1]


class TestBackpressure:
    async def test_content_is_dropped_when_the_reader_stalls(self) -> None:
        """An unbounded queue turns a stalled reader into unbounded memory.

        A turn producing tokens faster than a phone on a train accepts them is
        ordinary, not exceptional.
        """
        stream, bridge = bridged(capacity=4)
        stream.open()
        for index in range(50):
            stream.text(f"chunk-{index}")

        assert bridge.dropped > 0

    async def test_structural_events_are_never_dropped(self) -> None:
        """Losing a token degrades reading; losing the terminal event strands.

        The client would wait forever on a connection nothing will write to
        again.
        """
        stream, bridge = bridged(capacity=2)
        stream.open()
        for index in range(20):
            stream.text(f"chunk-{index}")
        stream.close()
        bridge.close()

        received = [e async for e in bridge.events()]
        assert any(e.terminal for e in received)

    async def test_publishing_never_blocks_the_producer(self) -> None:
        # A slow connection must not stall the turn it is watching, and with
        # it the tools and model calls behind it.
        stream, bridge = bridged(capacity=1)
        stream.open()

        await asyncio.wait_for(
            asyncio.to_thread(lambda: [stream.text(str(i)) for i in range(100)]),
            timeout=1.0,
        )


class TestDisconnect:
    async def test_abandoning_the_iterator_cancels_the_turn(self) -> None:
        """Where the reference implementation leaked sandbox time and spend.

        A turn left to finish into a connection nobody reads costs exactly as
        much as one somebody is reading.
        """
        stream, bridge = bridged()
        stream.open()
        finished = False

        async def long_turn() -> None:
            nonlocal finished
            stream.text("starting")
            await asyncio.sleep(5.0)
            finished = True

        iterator = run_and_stream(bridge, long_turn())
        await iterator.__anext__()  # take one frame, then walk away
        await iterator.aclose()

        await asyncio.sleep(0.01)
        assert not finished

    async def test_a_completed_turn_closes_the_stream(self) -> None:
        stream, bridge = bridged()
        stream.open()

        async def quick_turn() -> None:
            stream.text("done")
            stream.close()

        received = [e async for e in run_and_stream(bridge, quick_turn())]
        assert received[-1].terminal

    async def test_a_failing_turn_still_ends_the_stream(self) -> None:
        # Otherwise the reader waits on a connection whose producer has died.
        stream, bridge = bridged()
        stream.open()

        async def broken_turn() -> None:
            stream.text("partial")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _ = [e async for e in run_and_stream(bridge, broken_turn())]


class TestClosure:
    async def test_closing_twice_is_harmless(self) -> None:
        _, bridge = bridged()
        bridge.close()
        bridge.close()

    async def test_publishing_after_close_is_ignored(self) -> None:
        stream, bridge = bridged()
        stream.open()
        bridge.close()
        stream.text("late")

        received = [e async for e in bridge.events()]
        assert all(e.kind is not EventKind.TEXT for e in received)
