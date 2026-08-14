"""The events a running turn emits.

A turn is long. The client needs to see it happen rather than wait, which makes
the event stream part of the product rather than a transport detail — and makes
its ordering rules part of the contract.

Two properties matter enough to be enforced rather than documented.

The stream opens with metadata. A client that reconnects needs the run's
identifier to resume, and it cannot learn it from a stream it has not received
yet. Emitting anything before that identifier leaves a client that dropped the
connection at the wrong moment with no way back.

The stream ends exactly once, and terminally. A stream that ends twice makes a
client's completion handler run twice; one that never ends leaves it waiting on
a connection nothing will write to again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self
from uuid import UUID


class EventKind(StrEnum):
    """What an event says.

    Kept small on purpose. Every kind is something a client renders
    differently; anything a client would treat identically belongs as a field
    on an existing kind, not as a new one.
    """

    METADATA = "metadata"
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"
    USAGE = "usage"
    NOTICE = "notice"
    ERROR = "error"
    DONE = "done"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({EventKind.DONE, EventKind.ERROR})


class Severity(StrEnum):
    """How much a notice should interrupt the reader."""

    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, in order.

    `sequence` is assigned by the emitter and is contiguous per run. A client
    resuming after a drop asks for everything after the last sequence it saw,
    which only works if the numbering has no gaps.
    """

    kind: EventKind
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")

    @property
    def terminal(self) -> bool:
        return self.kind.terminal

    def to_wire(self) -> dict[str, Any]:
        return {"kind": str(self.kind), "seq": self.sequence, **self.payload}


class StreamViolation(RuntimeError):
    """The emitter tried to break the stream's contract.

    A programming error rather than a runtime condition: it means some code
    path emits in an order no client can consume.
    """


class EventStream:
    """Assigns sequence numbers and enforces the ordering contract.

    Deliberately does not do transport. What travels over an HTTP connection,
    what is buffered for reconnection and what is persisted for replay are
    three different questions, and answering them separately is what lets the
    same stream be replayed from storage later.
    """

    __slots__ = ("_run_id", "_sequence", "_opened", "_closed", "_recorded")

    def __init__(self, run_id: UUID, *, record: bool = True) -> None:
        self._run_id = run_id
        self._sequence = 0
        self._opened = False
        self._closed = False
        # Kept so a client that reconnects mid-turn can be caught up without
        # waiting for the turn to finish and be persisted.
        self._recorded: list[Event] = [] if record else []

    # -- lifecycle ---------------------------------------------------------

    def open(self, **metadata: Any) -> Event:
        """Emit the opening metadata. Must come first.

        Carries the run identifier, which is what a reconnecting client uses
        to resume. Emitting anything before it strands a client that dropped
        at the wrong moment.
        """
        if self._opened:
            raise StreamViolation("stream is already open")
        self._opened = True
        return self._emit(
            EventKind.METADATA, {"run_id": str(self._run_id), **metadata}
        )

    def close(self, **summary: Any) -> Event:
        """End the stream successfully."""
        self._require_open()
        if self._closed:
            raise StreamViolation("stream is already closed")
        self._closed = True
        return self._emit(EventKind.DONE, summary)

    def fail(self, message: str, *, retryable: bool = False, **detail: Any) -> Event:
        """End the stream with an error.

        Permitted even before `open`, because a failure during setup still has
        to reach the client — and a client that receives only an error knows
        more than one left holding an open connection.
        """
        if self._closed:
            raise StreamViolation("stream is already closed")
        self._closed = True
        self._opened = True
        return self._emit(
            EventKind.ERROR, {"message": message, "retryable": retryable, **detail}
        )

    # -- content -----------------------------------------------------------

    def text(self, chunk: str) -> Event:
        return self._content(EventKind.TEXT, {"text": chunk})

    def reasoning(self, chunk: str) -> Event:
        """A glimpse of the model's working, where the provider exposes it.

        Separate from `text` because it is not the answer: clients present it
        differently, and some suppress it entirely.
        """
        return self._content(EventKind.REASONING, {"text": chunk})

    def tool_call(self, call_id: str, name: str, arguments: dict[str, Any]) -> Event:
        return self._content(
            EventKind.TOOL_CALL,
            {"call_id": call_id, "name": name, "arguments": arguments},
        )

    def tool_result(
        self, call_id: str, *, ok: bool = True, summary: str = ""
    ) -> Event:
        """The outcome of a call, summarised.

        A summary rather than the result itself. Tool output can be enormous,
        and a client that only renders "searched 40 filings" should not have to
        receive forty filings to do it.
        """
        return self._content(
            EventKind.TOOL_RESULT, {"call_id": call_id, "ok": ok, "summary": summary}
        )

    def artifact(self, kind: str, reference: str, **detail: Any) -> Event:
        """Something produced and stored, referenced rather than inlined."""
        return self._content(
            EventKind.ARTIFACT, {"artifact_kind": kind, "ref": reference, **detail}
        )

    def usage(self, *, input_tokens: int, output_tokens: int, model: str) -> Event:
        """What the turn has cost so far.

        Streamed rather than reported at the end so a client can show cost
        accruing, and so a turn that dies still leaves a record of what it
        spent.
        """
        return self._content(
            EventKind.USAGE,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model,
            },
        )

    def notice(self, message: str, severity: Severity = Severity.INFO) -> Event:
        """Something the reader should know that is not part of the answer.

        Model fell back, quota nearly gone, history was condensed. These are
        the events that make a degraded turn legible instead of merely slow.
        """
        return self._content(
            EventKind.NOTICE, {"message": message, "severity": str(severity)}
        )

    # -- internals ---------------------------------------------------------

    def _content(self, kind: EventKind, payload: dict[str, Any]) -> Event:
        self._require_open()
        if self._closed:
            raise StreamViolation(f"cannot emit {kind} after the stream has ended")
        return self._emit(kind, payload)

    def _require_open(self) -> None:
        if not self._opened:
            raise StreamViolation(
                "stream must open with metadata; a client cannot resume a run "
                "whose identifier it never received"
            )

    def _emit(self, kind: EventKind, payload: dict[str, Any]) -> Event:
        event = Event(kind=kind, sequence=self._sequence, payload=payload)
        self._sequence += 1
        self._recorded.append(event)
        return event

    # -- inspection --------------------------------------------------------

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def recorded(self) -> tuple[Event, ...]:
        return tuple(self._recorded)

    def since(self, sequence: int) -> tuple[Event, ...]:
        """Everything after `sequence`, for a client that reconnected."""
        return tuple(e for e in self._recorded if e.sequence > sequence)


@dataclass(frozen=True, slots=True)
class Transcript:
    """A finished stream, for replay.

    Replay reads from here rather than from the live buffer: a turn watched
    live and the same turn opened tomorrow should render identically, and the
    only way to be sure is for both to come from the same record.
    """

    run_id: UUID
    events: tuple[Event, ...]

    @classmethod
    def of(cls, stream: EventStream) -> Self:
        return cls(run_id=stream.run_id, events=stream.recorded)

    @property
    def complete(self) -> bool:
        return bool(self.events) and self.events[-1].terminal

    @property
    def failed(self) -> bool:
        return bool(self.events) and self.events[-1].kind is EventKind.ERROR

    def text(self) -> str:
        """The answer, reassembled from its chunks."""
        return "".join(
            e.payload.get("text", "") for e in self.events if e.kind is EventKind.TEXT
        )

    def cost(self) -> tuple[int, int]:
        """Total tokens in and out, from the last usage event.

        Usage is cumulative, so the last one is the total. Summing them would
        count every earlier report again.
        """
        for event in reversed(self.events):
            if event.kind is EventKind.USAGE:
                return (
                    int(event.payload.get("input_tokens", 0)),
                    int(event.payload.get("output_tokens", 0)),
                )
        return (0, 0)

    def validate(self) -> None:
        """Check the invariants a consumer relies on.

        Run over persisted transcripts before replaying them: a record that
        violates the contract would make a replaying client behave differently
        from a live one, which is the hardest kind of bug to see.
        """
        if not self.events:
            raise StreamViolation("transcript is empty")

        if self.events[0].kind is not EventKind.METADATA:
            raise StreamViolation("transcript does not open with metadata")

        expected = [e.sequence for e in self.events]
        if expected != list(range(len(self.events))):
            raise StreamViolation(f"sequence numbers are not contiguous: {expected}")

        terminal_at = [i for i, e in enumerate(self.events) if e.terminal]
        if not terminal_at:
            raise StreamViolation("transcript never ends")
        if len(terminal_at) > 1:
            raise StreamViolation("transcript ends more than once")
        if terminal_at[0] != len(self.events) - 1:
            raise StreamViolation("transcript continues after ending")
