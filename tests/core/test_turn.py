"""Turn lifecycle: what may follow what, and what a turn owes when it stops."""

from __future__ import annotations

from uuid import uuid4

import pytest

from kairos.core.reasoning.turn import (
    Budget,
    InvalidTransition,
    Ledger,
    Phase,
    StopReason,
    ToolOutcome,
    ToolRequest,
    Turn,
    TurnRunner,
)
from kairos.core.streaming.events import EventKind, EventStream


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedTools:
    """Returns a fixed outcome per tool name, recording what it was asked."""

    def __init__(self, **outcomes: bool) -> None:
        self._ok = outcomes
        self.ran: list[str] = []

    async def run(self, request: ToolRequest) -> ToolOutcome:
        self.ran.append(request.name)
        ok = self._ok.get(request.name, True)
        return ToolOutcome(
            call_id=request.call_id,
            ok=ok,
            summary=f"{request.name} {'ok' if ok else 'failed'}",
            error="" if ok else "boom",
        )


def opened_stream() -> EventStream:
    stream = EventStream(uuid4())
    stream.open()
    return stream


def call(name: str) -> ToolRequest:
    return ToolRequest(call_id=f"c-{name}", name=name, arguments={})


class TestTransitions:
    def test_a_turn_starts_pending(self) -> None:
        assert Turn().phase is Phase.PENDING

    def test_the_ordinary_path(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.move_to(Phase.ACTING)
        turn.move_to(Phase.THINKING)
        turn.complete()
        assert turn.phase is Phase.COMPLETED

    def test_illegal_moves_are_refused(self) -> None:
        # Encoded rather than described: the reference implementation's live
        # path and recovery scanner drifted until they disagreed about what a
        # status meant.
        turn = Turn()
        with pytest.raises(InvalidTransition):
            turn.move_to(Phase.ACTING)

    def test_a_finished_turn_cannot_move(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.complete()
        with pytest.raises(InvalidTransition):
            turn.move_to(Phase.THINKING)

    @pytest.mark.parametrize(
        "phase", [Phase.COMPLETED, Phase.FAILED, Phase.CANCELLED]
    )
    def test_terminal_phases_are_terminal(self, phase: Phase) -> None:
        assert phase.terminal and not phase.live

    def test_cancelling_is_still_live(self) -> None:
        """Cancellation is not instantaneous.

        A tool already in flight has to finish or be abandoned, and the turn
        owes a settlement either way. Collapsing this into CANCELLED would lose
        the window in which that still has to happen.
        """
        assert Phase.CANCELLING.live and not Phase.CANCELLING.terminal


class TestCancellation:
    def test_requesting_cancel_moves_to_cancelling(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        assert turn.request_cancel()
        assert turn.phase is Phase.CANCELLING

    def test_requesting_twice_is_harmless(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.request_cancel()
        assert turn.request_cancel()  # idempotent, still True while live

    def test_cancelling_a_finished_turn_is_a_no_op(self) -> None:
        """A stop arriving after the turn ended is a race, not an error.

        The reference implementation raised here, which turned a harmless
        double-click into a 500.
        """
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.complete()
        assert turn.request_cancel() is False
        assert turn.phase is Phase.COMPLETED

    def test_a_cancelled_turn_records_why(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.request_cancel()
        turn.finish_cancelled()
        assert turn.stop_reason is StopReason.CANCELLED
        assert turn.settled


class TestSettlement:
    def test_a_live_turn_is_not_settled(self) -> None:
        assert not Turn().settled

    def test_terminal_without_a_reason_is_not_settled(self) -> None:
        """The pair matters.

        The reference implementation had rows in a terminal state with no
        reason, which its recovery scanner could not distinguish from rows it
        had itself abandoned.
        """
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.move_to(Phase.COMPLETED)  # bypassing complete(), so no reason
        assert turn.phase.terminal
        assert not turn.settled

    def test_a_failure_records_both(self) -> None:
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.fail("upstream refused")
        assert turn.settled
        assert turn.error == "upstream refused"


class TestBudget:
    def test_iterations_are_bounded(self) -> None:
        turn = Turn(budget=Budget(max_iterations=2))
        turn.ledger.record_model_call(input_tokens=1, output_tokens=1)
        assert turn.budget_exceeded(0.0) is None
        turn.ledger.record_model_call(input_tokens=1, output_tokens=1)
        assert turn.budget_exceeded(0.0) is StopReason.ITERATIONS

    def test_tokens_are_bounded(self) -> None:
        turn = Turn(budget=Budget(max_tokens=100))
        turn.ledger.record_model_call(input_tokens=60, output_tokens=50)
        assert turn.budget_exceeded(0.0) is StopReason.TOKENS

    def test_wall_time_is_bounded(self) -> None:
        # Catches a hung tool, which neither of the other two would.
        turn = Turn(budget=Budget(max_seconds=10.0))
        assert turn.budget_exceeded(11.0) is StopReason.TIME

    def test_a_degenerate_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one iteration"):
            Budget(max_iterations=0)

    def test_exhaustion_is_distinguishable_from_answering(self) -> None:
        assert StopReason.ITERATIONS.exhausted
        assert not StopReason.ANSWERED.exhausted


class TestLedger:
    def test_spend_is_recorded_regardless_of_outcome(self) -> None:
        """A turn that fails on its ninth call still cost eight.

        Recording spend only on success would make the platform's cheapest
        customers its most expensive.
        """
        ledger = Ledger()
        ledger.record_model_call(input_tokens=100, output_tokens=50)
        ledger.record_tool(ok=True)
        ledger.record_tool(ok=False)

        assert ledger.total_tokens == 150
        assert ledger.tool_calls == 2
        assert ledger.tool_failures == 1


class TestToolExecution:
    async def test_tools_run_in_order(self) -> None:
        """Sequential, because calls within a turn routinely depend on order.

        Read a file, then edit the thing you read. A model that wants them run
        together has no way to say so, and running them in parallel produces
        results that depend on scheduling.
        """
        tools = ScriptedTools()
        runner = TurnRunner(tools, opened_stream(), FakeClock())
        turn = Turn()

        await runner.run_tools(turn, [call("read"), call("edit"), call("verify")])

        assert tools.ran == ["read", "edit", "verify"]

    async def test_each_call_and_result_is_streamed(self) -> None:
        stream = opened_stream()
        runner = TurnRunner(ScriptedTools(), stream, FakeClock())

        await runner.run_tools(Turn(), [call("search")])

        kinds = [e.kind for e in stream.recorded]
        assert EventKind.TOOL_CALL in kinds
        assert EventKind.TOOL_RESULT in kinds

    async def test_a_failed_tool_does_not_end_the_turn(self) -> None:
        """Models routinely recover from a bad path or a malformed query.

        Ending here would turn a recoverable mistake into a failed turn the
        tenant still pays for.
        """
        tools = ScriptedTools(broken=False)
        runner = TurnRunner(tools, opened_stream(), FakeClock())
        turn = Turn()

        outcomes = await runner.run_tools(turn, [call("broken"), call("fine")])

        assert tools.ran == ["broken", "fine"]
        assert [o.ok for o in outcomes] == [False, True]
        assert turn.ledger.tool_failures == 1

    async def test_cancellation_stops_before_the_next_call(self) -> None:
        tools = ScriptedTools()
        runner = TurnRunner(tools, opened_stream(), FakeClock())
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.request_cancel()

        await runner.run_tools(turn, [call("first"), call("second")])

        assert tools.ran == []

    async def test_skipped_calls_are_announced(self) -> None:
        """A client that saw three announced and two arrive should be told why."""
        stream = opened_stream()
        runner = TurnRunner(ScriptedTools(), stream, FakeClock())
        turn = Turn()
        turn.move_to(Phase.THINKING)
        turn.request_cancel()

        await runner.run_tools(turn, [call("skipped")])

        notices = [e for e in stream.recorded if e.kind is EventKind.NOTICE]
        assert notices and "skipped" in notices[0].payload["message"]


class TestBudgetReporting:
    def test_exhaustion_is_announced_to_the_reader(self) -> None:
        """A truncated answer looks like a complete one unless you say so."""
        stream = opened_stream()
        runner = TurnRunner(ScriptedTools(), stream, FakeClock())

        runner.announce_exhaustion(StopReason.ITERATIONS)

        notices = [e for e in stream.recorded if e.kind is EventKind.NOTICE]
        assert notices[0].payload["severity"] == "warning"
        assert "step limit" in notices[0].payload["message"]

    def test_the_clock_drives_time_budget_checks(self) -> None:
        clock = FakeClock()
        runner = TurnRunner(ScriptedTools(), opened_stream(), clock)
        turn = Turn(budget=Budget(max_seconds=30.0))

        assert runner.check_budget(turn, started_at=0.0) is None
        clock.advance(31.0)
        assert runner.check_budget(turn, started_at=0.0) is StopReason.TIME
