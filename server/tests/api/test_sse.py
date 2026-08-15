"""SSE framing, and the reconnect cursor."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from kairos.api.stream.sse import (
    KEEPALIVE,
    SSE_HEADERS,
    ResumePoint,
    encode,
    replay,
    stream_events,
    wire_summary,
)
from kairos.core.streaming.events import (
    Event,
    EventKind,
    EventStream,
    StreamViolation,
    Transcript,
)


def transcript_of(*texts: str) -> Transcript:
    stream = EventStream(uuid4())
    stream.open()
    for text in texts:
        stream.text(text)
    stream.close()
    return Transcript.of(stream)


class TestFraming:
    def test_a_frame_carries_id_event_and_data(self) -> None:
        frame = encode(Event(kind=EventKind.TEXT, sequence=7, payload={"text": "hi"}))
        assert "id: 7" in frame
        assert "event: text" in frame
        assert "data: " in frame

    def test_a_frame_ends_with_a_blank_line(self) -> None:
        # Without it the client never dispatches the event.
        assert encode(Event(kind=EventKind.DONE, sequence=0)).endswith("\n\n")

    def test_embedded_newlines_get_their_own_data_prefix(self) -> None:
        """A payload newline written as one line ends the frame early.

        Everything after it is then parsed as the next event's fields.
        """
        frame = encode(
            Event(kind=EventKind.TEXT, sequence=0, payload={"text": "a\nb"})
        )
        # json escapes the newline, so the wire form stays single-line — assert
        # the property that matters rather than the mechanism.
        body = "".join(
            line[len("data: ") :] for line in frame.splitlines() if line.startswith("data: ")
        )
        assert json.loads(body)["text"] == "a\nb"

    def test_unicode_is_not_escaped(self) -> None:
        # Escaping would triple the size of a Chinese answer for no benefit.
        frame = encode(Event(kind=EventKind.TEXT, sequence=0, payload={"text": "报价"}))
        assert "报价" in frame

    def test_the_wire_form_round_trips(self) -> None:
        frame = encode(Event(kind=EventKind.USAGE, sequence=3, payload={"input_tokens": 10}))
        body = next(
            line[len("data: ") :] for line in frame.splitlines() if line.startswith("data: ")
        )
        assert json.loads(body) == {"kind": "usage", "seq": 3, "input_tokens": 10}


class TestHeaders:
    def test_buffering_is_disabled(self) -> None:
        """A buffering proxy holds the stream until it ends.

        For a turn that takes a minute, the reader sees nothing for a minute
        and then everything at once.
        """
        assert SSE_HEADERS["X-Accel-Buffering"] == "no"

    def test_transformation_is_refused(self) -> None:
        assert "no-transform" in SSE_HEADERS["Cache-Control"]

    def test_keepalive_is_a_comment_not_an_event(self) -> None:
        # So clients need no special case for a synthetic type.
        assert KEEPALIVE.startswith(":")


class TestResumePoint:
    def test_the_query_parameter_wins_over_the_header(self) -> None:
        """A fetch-based client must pass the cursor itself.

        The header is what the standard specifies and what EventSource sends
        automatically — but EventSource cannot send an authorization header, so
        an authenticated stream is read with fetch instead.
        """
        point = ResumePoint.from_request(query_value="5", header_value="2")
        assert point.last_sequence == 5

    def test_the_header_is_used_when_the_query_is_absent(self) -> None:
        assert ResumePoint.from_request(header_value="3").last_sequence == 3

    @pytest.mark.parametrize("bad", ["", "abc", "-1", "3.5", None])
    def test_a_malformed_cursor_is_ignored(self, bad: str | None) -> None:
        """Replaying from the start is wasteful; refusing strands the reader."""
        assert ResumePoint.from_request(query_value=bad).last_sequence is None

    def test_zero_is_a_valid_cursor(self) -> None:
        # The client saw the metadata frame and nothing else.
        assert ResumePoint.from_request(query_value="0").last_sequence == 0

    def test_resuming_returns_only_later_events(self) -> None:
        transcript = transcript_of("a", "b", "c")
        resumed = ResumePoint(last_sequence=1).resume_from(transcript)
        assert [e.sequence for e in resumed] == [2, 3, 4]

    def test_no_cursor_returns_everything(self) -> None:
        transcript = transcript_of("a")
        assert len(ResumePoint().resume_from(transcript)) == len(transcript.events)

    def test_a_cursor_past_the_end_returns_nothing(self) -> None:
        assert ResumePoint(last_sequence=999).resume_from(transcript_of("a")) == ()


class TestReplay:
    def test_a_valid_transcript_renders(self) -> None:
        rendered = replay(transcript_of("hello"))
        assert "event: metadata" in rendered
        assert "event: done" in rendered

    def test_replay_honours_the_cursor(self) -> None:
        rendered = replay(transcript_of("a", "b"), ResumePoint(last_sequence=2))
        assert "event: metadata" not in rendered

    def test_an_invalid_transcript_is_refused(self) -> None:
        """A replaying client must not diverge from a live one.

        That divergence only shows up on the second viewing, which makes it the
        hardest kind of bug to notice.
        """
        broken = Transcript(
            run_id=uuid4(),
            events=(Event(kind=EventKind.TEXT, sequence=0),),  # no metadata, never ends
        )
        with pytest.raises(StreamViolation):
            replay(broken)


class TestErrorFraming:
    async def test_an_exception_becomes_a_terminal_frame(self) -> None:
        """By generator time the client holds an open 200.

        Letting the exception escape closes it with no explanation, and the
        client cannot tell that from a completed turn.
        """

        async def failing() -> AsyncIterator[Event]:
            yield Event(kind=EventKind.METADATA, sequence=0)
            raise RuntimeError("upstream died")

        frames = [frame async for frame in stream_events(failing())]

        assert "event: metadata" in frames[0]
        assert "event: error" in frames[-1]
        assert "upstream died" in frames[-1]

    async def test_the_error_frame_continues_the_sequence(self) -> None:
        async def failing() -> AsyncIterator[Event]:
            yield Event(kind=EventKind.TEXT, sequence=4)
            raise RuntimeError("boom")

        frames = [frame async for frame in stream_events(failing())]
        assert "id: 5" in frames[-1]

    async def test_a_clean_stream_is_passed_through(self) -> None:
        async def fine() -> AsyncIterator[Event]:
            yield Event(kind=EventKind.METADATA, sequence=0)
            yield Event(kind=EventKind.DONE, sequence=1)

        frames = [frame async for frame in stream_events(fine())]
        assert len(frames) == 2
        assert "error" not in frames[-1]


class TestPayloadShrinking:
    def test_long_strings_are_truncated_and_flagged(self) -> None:
        shrunk = wire_summary({"result": "x" * 5000}, limit=100)
        assert shrunk["result_truncated"] is True
        assert len(shrunk["result"]) == 101  # 100 + ellipsis

    def test_short_values_are_untouched(self) -> None:
        assert wire_summary({"ok": True, "n": 5}) == {"ok": True, "n": 5}
