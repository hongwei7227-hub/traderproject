"""The loop that answers a question.

Ask the model. If it asked for tools, run them and ask again with the results.
Repeat until it answers without asking for anything, or until a budget stops
it. That is the whole algorithm; everything else here is about the ways it can
end badly.

The engine depends on ports, not on the things behind them. Whether the model
call goes over HTTP or comes from a fixture, whether tools run in a container
or in a dictionary, the loop is the same — which is what makes the interesting
cases (budget exhaustion, cancellation mid-tool, a model that loops) testable
without a network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from kairos.core.catalog.descriptors import ModelId
from kairos.core.quota.reservation import (
    Estimate,
    QuotaDecision,
    Reservation,
    Settlement,
)
from kairos.core.reasoning.turn import (
    Budget,
    Phase,
    StopReason,
    ToolOutcome,
    ToolRequest,
    ToolRunner,
    Turn,
    TurnRunner,
)
from kairos.core.streaming.events import EventStream, Severity
from kairos.core.tenancy.context import TenantId
from kairos.core.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ModelReply:
    """What one model call produced."""

    text: str = ""
    reasoning: str = ""
    tool_requests: tuple[ToolRequest, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""
    degraded: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_requests)


class ModelCaller(Protocol):
    """Calls a model with the conversation so far.

    A port rather than the invoker itself, so the loop can be exercised with a
    scripted sequence of replies. The adapter behind it owns retries, fallback
    and circuit breaking; the loop never sees those.
    """

    async def call(
        self, messages: Sequence[dict[str, object]], tools: Sequence[dict[str, object]]
    ) -> ModelReply: ...


class QuotaGate(Protocol):
    """Reserves budget before work and settles after."""

    async def reserve(self, tenant: TenantId, estimate: Estimate) -> QuotaDecision: ...

    async def settle(self, settlement: Settlement) -> None: ...

    async def abandon(self, reservation: Reservation) -> None: ...


@dataclass(slots=True)
class Conversation:
    """The messages so far, in the shape the model layer expects."""

    messages: list[dict[str, object]] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, reply: ModelReply) -> None:
        entry: dict[str, object] = {"role": "assistant", "content": reply.text}
        if reply.wants_tools:
            entry["tool_calls"] = [
                {"id": r.call_id, "name": r.name, "arguments": r.arguments}
                for r in reply.tool_requests
            ]
        self.messages.append(entry)

    def add_tool_results(self, outcomes: Sequence[ToolOutcome]) -> None:
        for outcome in outcomes:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": outcome.call_id,
                    "content": outcome.summary if outcome.ok else f"error: {outcome.error}",
                }
            )

    def __len__(self) -> int:
        return len(self.messages)


@dataclass(frozen=True, slots=True)
class Admission:
    """Whether a turn may start, and what it reserved to do so."""

    proceed: bool
    reservation: Reservation | None


@dataclass(slots=True)
class Answer:
    """What a finished turn produced."""

    turn: Turn
    text: str
    served_by: str = ""

    @property
    def complete(self) -> bool:
        return self.turn.phase is Phase.COMPLETED

    @property
    def truncated(self) -> bool:
        """Whether the answer stops short of what was asked.

        A budget-exhausted turn returns prose that reads like a finished
        answer. Callers that present it as one are lying by omission.
        """
        return self.turn.stop_reason is not None and self.turn.stop_reason.exhausted


class ReasoningEngine:
    """Runs turns."""

    __slots__ = ("_model", "_registry", "_quota", "_clock")

    def __init__(
        self,
        model: ModelCaller,
        registry: ToolRegistry,
        quota: QuotaGate | None = None,
        clock=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self._model = model
        self._registry = registry
        # Optional: a self-hosted deployment metering nothing still needs the
        # loop. Making it required would force a null implementation on every
        # such deployment.
        self._quota = quota
        self._clock = clock or _monotonic

    async def run(
        self,
        prompt: str,
        *,
        tenant: TenantId,
        tools: ToolRunner,
        stream: EventStream,
        budget: Budget | None = None,
        model_hint: ModelId | None = None,
    ) -> Answer:
        turn = Turn(tenant=tenant, budget=budget or Budget())
        conversation = Conversation()
        conversation.add_user(prompt)

        started_at = self._clock()
        runner = TurnRunner(tools, stream, self._clock)

        admission = await self._reserve(turn, tenant, prompt, stream)
        if not admission.proceed:
            return Answer(turn=turn, text="")
        reservation = admission.reservation

        answer = ""
        served_by = ""

        try:
            while True:
                if turn.cancel_requested:
                    turn.finish_cancelled()
                    break

                if (reason := runner.check_budget(turn, started_at)) is not None:
                    runner.announce_exhaustion(reason)
                    turn.fail("budget exhausted", reason)
                    break

                turn.move_to(Phase.THINKING)
                reply = await self._model.call(
                    conversation.messages, self._registry.schemas()
                )
                turn.ledger.record_model_call(
                    input_tokens=reply.input_tokens, output_tokens=reply.output_tokens
                )
                served_by = reply.model_id or served_by

                if reply.degraded:
                    # A fallback answer can read noticeably differently. A
                    # silent substitution looks like the model getting worse.
                    stream.notice("answered by a fallback model", Severity.WARNING)

                if reply.reasoning:
                    stream.reasoning(reply.reasoning)
                if reply.text:
                    stream.text(reply.text)
                    answer = reply.text

                stream.usage(
                    input_tokens=turn.ledger.input_tokens,
                    output_tokens=turn.ledger.output_tokens,
                    model=served_by,
                )
                conversation.add_assistant(reply)

                if not reply.wants_tools:
                    turn.complete()
                    break

                rejected = self._reject_unavailable(reply.tool_requests, stream)
                permitted = [
                    r for r in reply.tool_requests if r.call_id not in rejected
                ]

                turn.move_to(Phase.ACTING)
                outcomes = await runner.run_tools(turn, permitted)
                conversation.add_tool_results([*rejected.values(), *outcomes])

                if turn.cancel_requested:
                    turn.finish_cancelled()
                    break

        except Exception as failure:  # noqa: BLE001 — recorded, then re-raised
            # The turn is settled before the exception leaves, so that a crash
            # cannot leave a row that the recovery scanner will later find in
            # a live phase with no owner.
            if not turn.phase.terminal:
                turn.fail(str(failure))
            await self._settle(reservation, turn)
            stream.fail(str(failure))
            raise
        finally:
            if turn.phase is Phase.CANCELLING:
                # Cancellation that arrived between the check and the break.
                turn.finish_cancelled()

        await self._settle(reservation, turn)
        self._close_stream(stream, turn, answer)
        return Answer(turn=turn, text=answer, served_by=served_by)

    # -- quota -------------------------------------------------------------

    async def _reserve(
        self, turn: Turn, tenant: TenantId, prompt: str, stream: EventStream
    ) -> Admission:
        # Two distinct "no reservation" cases, which is why this returns a
        # result rather than an optional: an unmetered deployment proceeds
        # without one, a blocked tenant does not proceed at all.
        if self._quota is None:
            return Admission(proceed=True, reservation=None)

        estimate = Estimate(
            prompt_tokens=_estimate_tokens(prompt),
            max_output_tokens=turn.budget.max_tokens,
        )
        decision = await self._quota.reserve(tenant, estimate)

        for warning in decision.warnings:
            stream.notice(warning, Severity.WARNING)

        if decision.blocked or decision.reservation is None:
            turn.fail(decision.reason or "quota exhausted", StopReason.ERROR)
            stream.fail(decision.reason or "quota exhausted", retryable=False)
            return Admission(proceed=False, reservation=None)

        if decision.degrade:
            stream.notice(decision.reason, Severity.WARNING)

        return Admission(proceed=True, reservation=decision.reservation)

    async def _settle(self, reservation: Reservation | None, turn: Turn) -> None:
        if self._quota is None or reservation is None:
            return
        await self._quota.settle(
            Settlement(
                reservation=reservation,
                input_tokens=turn.ledger.input_tokens,
                output_tokens=turn.ledger.output_tokens,
            )
        )

    # -- tools -------------------------------------------------------------

    def _reject_unavailable(
        self, requests: Sequence[ToolRequest], stream: EventStream
    ) -> dict[str, ToolOutcome]:
        """Refuse calls the registry does not offer, without ending the turn.

        A model asking for a tool it cannot have is common — a stale prompt, a
        capability withdrawn mid-session. Telling it so lets it choose
        something else; failing the turn makes the tenant pay for a mistake the
        model can recover from on its own.
        """
        # Keyed by call id rather than by request: a request carries its
        # arguments dict, so it is not hashable.
        rejected: dict[str, ToolOutcome] = {}
        for request in requests:
            if self._registry.accepts(request):
                continue
            outcome = ToolOutcome(
                call_id=request.call_id,
                ok=False,
                summary=f"{request.name} is not available",
                error=f"no such tool: {request.name}",
            )
            stream.tool_result(request.call_id, ok=False, summary=outcome.summary)
            rejected[request.call_id] = outcome
        return rejected

    # -- reporting ---------------------------------------------------------

    @staticmethod
    def _close_stream(stream: EventStream, turn: Turn, answer: str) -> None:
        if stream.closed:
            return
        stream.close(
            phase=str(turn.phase),
            reason=str(turn.stop_reason) if turn.stop_reason else "",
            characters=len(answer),
        )


def _estimate_tokens(text: str) -> int:
    """A rough token count, deliberately generous.

    Reservation is pessimistic by design: under-reserving lets a burst of
    concurrent turns each pass a check and collectively overrun. Four
    characters per token is close enough for English and errs high for the
    dense text where it is wrong.
    """
    return max(1, len(text) // 4)


def _monotonic() -> float:
    import time

    return time.monotonic()
