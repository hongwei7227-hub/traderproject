"""One turn of an agent conversation.

A turn is the unit of work this platform bills, streams, cancels and resumes.
It runs a loop: ask the model, run whatever tools it asked for, ask again with
the results, until the model answers without calling anything.

The loop itself is small. What makes it work is the bookkeeping around it —
knowing when to stop, what a cancellation means halfway through, and what a
turn that died still owes the tenant. The reference implementation spread that
bookkeeping across twenty-five middleware layers whose order was maintained by
comment; here it is one explicit state machine with the invariants written as
assertions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from kairos.core.streaming.events import EventStream, Severity
from kairos.core.tenancy.context import TenantId


class Phase(StrEnum):
    """Where a turn is.

    `CANCELLING` exists because cancellation is not instantaneous: a tool call
    already in flight has to finish or be abandoned, and the turn owes a
    settlement either way. Collapsing it into `CANCELLED` would lose the window
    in which that still has to happen.
    """

    PENDING = "pending"
    THINKING = "thinking"
    ACTING = "acting"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def live(self) -> bool:
        return self in _LIVE


_TERMINAL = frozenset({Phase.COMPLETED, Phase.FAILED, Phase.CANCELLED})
_LIVE = frozenset({Phase.PENDING, Phase.THINKING, Phase.ACTING, Phase.CANCELLING})

# Which phases may follow which. Encoded rather than described, because the
# reference implementation's status handling drifted between the live path and
# the recovery scanner until they disagreed about what "interrupted" meant.
_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.PENDING: frozenset({Phase.THINKING, Phase.CANCELLING, Phase.FAILED}),
    Phase.THINKING: frozenset({Phase.ACTING, Phase.COMPLETED, Phase.FAILED, Phase.CANCELLING}),
    Phase.ACTING: frozenset({Phase.THINKING, Phase.FAILED, Phase.CANCELLING}),
    Phase.CANCELLING: frozenset({Phase.CANCELLED, Phase.FAILED}),
    Phase.COMPLETED: frozenset(),
    Phase.FAILED: frozenset(),
    Phase.CANCELLED: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, current: Phase, requested: Phase) -> None:
        super().__init__(f"cannot move from {current} to {requested}")


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """A tool the model asked to run."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What running one produced.

    `summary` is what goes back into the conversation and out to the client;
    `payload` is the full result, which may be very large and is stored rather
    than inlined. Keeping them separate is what stops one enormous tool result
    from consuming the context window the rest of the turn needs.
    """

    call_id: str
    ok: bool
    summary: str
    payload: Any = None
    error: str = ""


class ToolRunner(Protocol):
    async def run(self, request: ToolRequest) -> ToolOutcome: ...


@dataclass(frozen=True, slots=True)
class Budget:
    """What a turn may spend before it is stopped.

    Three limits rather than one because they fail differently. Iterations
    catch a model looping over the same tool; tokens catch a turn that is
    making progress but costing too much; wall time catches a tool that hangs.
    """

    max_iterations: int = 12
    max_tokens: int = 500_000
    max_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("a turn needs at least one iteration")


@dataclass(slots=True)
class Ledger:
    """What a turn has spent, kept whether or not it succeeds.

    A turn that fails on its ninth tool call has still cost eight tool calls
    and the tokens that went with them. Recording spend only on success would
    make the platform's cheapest customers its most expensive.
    """

    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_failures: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record_model_call(self, *, input_tokens: int, output_tokens: int) -> None:
        self.iterations += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def record_tool(self, *, ok: bool) -> None:
        self.tool_calls += 1
        if not ok:
            self.tool_failures += 1


class StopReason(StrEnum):
    ANSWERED = "answered"
    ITERATIONS = "iteration_budget"
    TOKENS = "token_budget"
    TIME = "time_budget"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def exhausted(self) -> bool:
        return self in {StopReason.ITERATIONS, StopReason.TOKENS, StopReason.TIME}


@dataclass(slots=True)
class Turn:
    """The state of one turn, and the rules for changing it."""

    id: UUID = field(default_factory=uuid4)
    tenant: TenantId | None = None
    phase: Phase = Phase.PENDING
    budget: Budget = field(default_factory=Budget)
    ledger: Ledger = field(default_factory=Ledger)
    stop_reason: StopReason | None = None
    error: str = ""
    cancel_requested: bool = False

    # -- transitions -------------------------------------------------------

    def move_to(self, phase: Phase) -> None:
        if phase not in _TRANSITIONS[self.phase]:
            raise InvalidTransition(self.phase, phase)
        self.phase = phase

    def request_cancel(self) -> bool:
        """Ask the turn to stop.

        Idempotent, and a no-op once the turn has ended — a stop arriving after
        a turn finished is a race, not an error, and the reference
        implementation's habit of raising here turned a harmless double-click
        into a 500.
        """
        if self.phase.terminal:
            return False
        self.cancel_requested = True
        if self.phase is not Phase.CANCELLING:
            self.move_to(Phase.CANCELLING)
        return True

    def complete(self) -> None:
        self.move_to(Phase.COMPLETED)
        self.stop_reason = StopReason.ANSWERED

    def fail(self, error: str, reason: StopReason = StopReason.ERROR) -> None:
        self.move_to(Phase.FAILED)
        self.stop_reason = reason
        self.error = error

    def finish_cancelled(self) -> None:
        self.move_to(Phase.CANCELLED)
        self.stop_reason = StopReason.CANCELLED

    # -- budget ------------------------------------------------------------

    def budget_exceeded(self, elapsed_seconds: float) -> StopReason | None:
        """Which budget, if any, this turn has run past."""
        if self.ledger.iterations >= self.budget.max_iterations:
            return StopReason.ITERATIONS
        if self.ledger.total_tokens >= self.budget.max_tokens:
            return StopReason.TOKENS
        if elapsed_seconds >= self.budget.max_seconds:
            return StopReason.TIME
        return None

    # -- reporting ---------------------------------------------------------

    @property
    def settled(self) -> bool:
        """Whether this turn owes anything further.

        A turn is settled when it has both reached a terminal phase and
        recorded why. The pair matters: the reference implementation had rows
        in a terminal state with no reason, which its recovery scanner then
        could not distinguish from rows it had itself abandoned.
        """
        return self.phase.terminal and self.stop_reason is not None


class TurnRunner:
    """Drives a turn to completion.

    Takes its collaborators as arguments — the thing that calls the model, the
    thing that runs tools, the stream to report on — so that the loop can be
    tested against fakes and so that none of them can reach for each other.
    """

    __slots__ = ("_tools", "_stream", "_clock")

    def __init__(self, tools: ToolRunner, stream: EventStream, clock) -> None:  # type: ignore[no-untyped-def]
        self._tools = tools
        self._stream = stream
        # Injected so budget expiry can be tested without waiting for it.
        self._clock = clock

    async def run_tools(self, turn: Turn, requests: Sequence[ToolRequest]) -> list[ToolOutcome]:
        """Run the tools a model asked for, in order.

        Sequential rather than concurrent: tool calls within one turn routinely
        depend on each other — read a file, then edit the thing you read — and
        a model that wants them run together has no way to say so. Running them
        in parallel to save time produces results that depend on scheduling.
        """
        outcomes: list[ToolOutcome] = []

        for request in requests:
            if turn.cancel_requested:
                # Report what was skipped rather than silently truncating: a
                # client that saw three tool calls announced and two results
                # arrive should be told why.
                self._stream.notice(
                    f"stopped before running {request.name}", Severity.WARNING
                )
                break

            self._stream.tool_call(request.call_id, request.name, request.arguments)
            outcome = await self._tools.run(request)
            turn.ledger.record_tool(ok=outcome.ok)
            self._stream.tool_result(
                outcome.call_id, ok=outcome.ok, summary=outcome.summary
            )
            outcomes.append(outcome)

            # A failed tool does not end the turn. Models routinely recover —
            # a bad path, a malformed query — and ending here would turn a
            # recoverable mistake into a failed turn the tenant still pays for.

        return outcomes

    def check_budget(self, turn: Turn, started_at: float) -> StopReason | None:
        elapsed = self._clock() - started_at
        return turn.budget_exceeded(elapsed)

    def announce_exhaustion(self, reason: StopReason) -> None:
        """Tell the reader why a turn stopped short.

        Budget exhaustion produces a partial answer that looks like a complete
        one. Saying so is the difference between a limit and a bug.
        """
        message = {
            StopReason.ITERATIONS: "stopped after reaching the step limit",
            StopReason.TOKENS: "stopped after reaching the token limit",
            StopReason.TIME: "stopped after reaching the time limit",
        }.get(reason, "stopped")
        self._stream.notice(message, Severity.WARNING)
