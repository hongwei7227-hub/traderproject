"""Server-sent events, on the wire.

The event protocol says what a turn emits and in what order; this says how that
travels. The two are separate because the same events are also replayed from
storage later, and a replayed turn must render identically to a live one —
which only holds if the wire format is a projection of the events rather than
the place their meaning lives.

The framing rules here are not decorative. A payload containing a newline that
is written as one line ends the event early, and everything after it is parsed
as the next event's fields.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Final

from kairos.core.streaming.events import Event, Transcript

# Proxies that buffer will hold a stream until it ends, which for a turn that
# takes a minute means the reader sees nothing for a minute and then
# everything. The header is advisory but widely honoured.
SSE_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Sent when nothing has happened for a while. A comment line is a valid SSE
# frame that carries no event, so it keeps intermediaries from closing an idle
# connection without the client having to ignore a synthetic event type.
KEEPALIVE: Final[str] = ": keepalive\n\n"


def encode(event: Event) -> str:
    """Render one event as an SSE frame.

    `id` carries the sequence number, which is what a reconnecting client sends
    back so the server knows where to resume.
    """
    payload = json.dumps(event.to_wire(), ensure_ascii=False, separators=(",", ":"))
    lines = [f"id: {event.sequence}", f"event: {event.kind}"]
    # A data field is per-line: embedded newlines must each get their own
    # `data:` prefix or the frame ends at the first one.
    lines.extend(f"data: {chunk}" for chunk in payload.split("\n"))
    return "\n".join(lines) + "\n\n"


def encode_all(events: Iterable[Event]) -> str:
    return "".join(encode(event) for event in events)


@dataclass(frozen=True, slots=True)
class ResumePoint:
    """Where a reconnecting client left off."""

    last_sequence: int | None = None

    @classmethod
    def from_request(
        cls, *, query_value: str | None = None, header_value: str | None = None
    ) -> ResumePoint:
        """Read the resume cursor from a request.

        The query parameter wins over the header. Both exist because the header
        is what the standard specifies and what a browser's own EventSource
        sends automatically — but a client that reads the stream with fetch,
        which is the only way to send an authorization header alongside it,
        must pass the cursor itself.

        A malformed cursor is ignored rather than rejected: replaying a turn
        from the beginning is correct, just wasteful, whereas refusing the
        reconnect strands a reader who is not at fault.
        """
        for candidate in (query_value, header_value):
            if candidate is None:
                continue
            try:
                parsed = int(candidate)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                return cls(last_sequence=parsed)
        return cls()

    def resume_from(self, transcript: Transcript) -> tuple[Event, ...]:
        """The events this client has not seen."""
        if self.last_sequence is None:
            return transcript.events
        return tuple(e for e in transcript.events if e.sequence > self.last_sequence)


class SseError(Exception):
    """A failure that must reach the client as an event, not a status code.

    Once the response has begun the status line is already sent, so an error
    raised from inside the generator cannot become a 500 — the client would see
    a truncated 200. It has to be framed as a terminal event instead.
    """


async def stream_events(
    events: AsyncIterator[Event], *, on_error: str = "stream failed"
) -> AsyncIterator[str]:
    """Turn a stream of events into SSE frames.

    Any exception is converted into a terminal error frame rather than
    propagating: by the time the generator runs, the client is holding an open
    200 response, and letting the exception escape closes it with no
    explanation.
    """
    from kairos.core.streaming.events import Event as _Event
    from kairos.core.streaming.events import EventKind

    sequence = -1
    try:
        async for event in events:
            sequence = event.sequence
            yield encode(event)
    except Exception as failure:  # noqa: BLE001 — must not escape a live response
        yield encode(
            _Event(
                kind=EventKind.ERROR,
                sequence=sequence + 1,
                payload={"message": on_error, "detail": str(failure)[:300]},
            )
        )


def replay(transcript: Transcript, resume: ResumePoint | None = None) -> str:
    """Render a stored transcript for a client catching up.

    Validated first: a transcript that violates the ordering contract would
    make a replaying client behave differently from a live one, and that
    divergence is the hardest kind of bug to see because it only appears on the
    second viewing.
    """
    transcript.validate()
    events = (resume or ResumePoint()).resume_from(transcript)
    return encode_all(events)


def wire_summary(payload: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Shrink a payload that would otherwise dominate the stream.

    Applied to tool results, which can be enormous. The client renders a
    summary; anything wanting the whole thing fetches it by reference.
    """
    shrunk: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and len(value) > limit:
            shrunk[key] = f"{value[:limit]}…"
            shrunk[f"{key}_truncated"] = True
        else:
            shrunk[key] = value
    return shrunk
