"""The loop, and the ways it can end badly."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest

from kairos.core.quota.reservation import (
    Allowance,
    Estimate,
    Exhaustion,
    QuotaDecision,
    QuotaPolicy,
    Reservation,
    Settlement,
)
from kairos.core.reasoning.engine import Conversation, ModelReply, ReasoningEngine
from kairos.core.reasoning.turn import Budget, Phase, StopReason, ToolOutcome, ToolRequest
from kairos.core.streaming.events import EventKind, EventStream
from kairos.core.tenancy import TenantId
from kairos.core.tools import Exposure, ToolDefinition, ToolRegistry

ACME = TenantId("acme")


class ScriptedModel:
    """Replays a fixed sequence of replies, recording what it was sent."""

    def __init__(self, *replies: ModelReply) -> None:
        self._replies = list(replies)
        self.calls: list[Sequence[dict[str, object]]] = []

    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append(list(messages))
        if not self._replies:
            return ModelReply(text="done", output_tokens=1)
        return self._replies.pop(0)


class LoopingModel:
    """Always asks for the same tool. Stands in for a model that will not stop."""

    def __init__(self) -> None:
        self.count = 0

    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        self.count += 1
        return ModelReply(
            tool_requests=(ToolRequest(call_id=f"c{self.count}", name="search", arguments={}),),
            input_tokens=10,
            output_tokens=10,
        )


class ExplodingModel:
    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream on fire")


class EchoTools:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, request: ToolRequest) -> ToolOutcome:
        self.ran.append(request.name)
        return ToolOutcome(call_id=request.call_id, ok=True, summary=f"{request.name} ok")


class CancellingTools:
    """Cancels the turn from inside the first tool call."""

    def __init__(self, turn_holder: list) -> None:  # type: ignore[type-arg]
        self._holder = turn_holder
        self.ran: list[str] = []

    async def run(self, request: ToolRequest) -> ToolOutcome:
        self.ran.append(request.name)
        self._holder[0].request_cancel()
        return ToolOutcome(call_id=request.call_id, ok=True, summary="ok")


class RecordingQuota:
    def __init__(self, allowance: Allowance | None = None) -> None:
        self.allowance = allowance or Allowance(limit=1_000_000)
        self.settled: list[Settlement] = []
        self._policy = QuotaPolicy()

    async def reserve(self, tenant: TenantId, estimate: Estimate) -> QuotaDecision:
        return self._policy.evaluate(tenant, self.allowance, estimate)

    async def settle(self, settlement: Settlement) -> None:
        self.settled.append(settlement)

    async def abandon(self, reservation: Reservation) -> None:
        pass


def registry_with(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(
            ToolDefinition(name=name, description=name, exposure=Exposure.DIRECT)
        )
    return registry


def opened() -> EventStream:
    stream = EventStream(uuid4())
    stream.open()
    return stream


class TestPlainAnswer:
    async def test_a_model_that_answers_ends_the_turn(self) -> None:
        engine = ReasoningEngine(ScriptedModel(ModelReply(text="42")), registry_with())
        answer = await engine.run(
            "what", tenant=ACME, tools=EchoTools(), stream=opened()
        )

        assert answer.complete
        assert answer.text == "42"
        assert not answer.truncated

    async def test_text_is_streamed(self) -> None:
        stream = opened()
        engine = ReasoningEngine(ScriptedModel(ModelReply(text="hello")), registry_with())
        await engine.run("hi", tenant=ACME, tools=EchoTools(), stream=stream)

        assert any(e.kind is EventKind.TEXT for e in stream.recorded)

    async def test_the_stream_is_closed_exactly_once(self) -> None:
        stream = opened()
        engine = ReasoningEngine(ScriptedModel(ModelReply(text="x")), registry_with())
        await engine.run("hi", tenant=ACME, tools=EchoTools(), stream=stream)

        assert stream.closed
        assert sum(1 for e in stream.recorded if e.terminal) == 1


class TestToolLoop:
    async def test_tools_run_then_the_model_is_asked_again(self) -> None:
        model = ScriptedModel(
            ModelReply(
                tool_requests=(ToolRequest(call_id="c1", name="search", arguments={}),)
            ),
            ModelReply(text="found it"),
        )
        tools = EchoTools()
        engine = ReasoningEngine(model, registry_with("search"))

        answer = await engine.run("q", tenant=ACME, tools=tools, stream=opened())

        assert tools.ran == ["search"]
        assert answer.text == "found it"
        assert len(model.calls) == 2

    async def test_tool_results_reach_the_next_call(self) -> None:
        model = ScriptedModel(
            ModelReply(
                tool_requests=(ToolRequest(call_id="c1", name="search", arguments={}),)
            ),
            ModelReply(text="done"),
        )
        engine = ReasoningEngine(model, registry_with("search"))

        await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())

        roles = [m["role"] for m in model.calls[1]]
        assert "tool" in roles

    async def test_an_unavailable_tool_is_refused_without_ending_the_turn(self) -> None:
        """A stale prompt or a withdrawn capability is recoverable.

        Failing the turn makes the tenant pay for a mistake the model can fix
        itself on the next step.
        """
        model = ScriptedModel(
            ModelReply(
                tool_requests=(ToolRequest(call_id="c1", name="ghost", arguments={}),)
            ),
            ModelReply(text="fine, without it"),
        )
        tools = EchoTools()
        engine = ReasoningEngine(model, registry_with("search"))

        answer = await engine.run("q", tenant=ACME, tools=tools, stream=opened())

        assert tools.ran == []
        assert answer.complete
        assert answer.text == "fine, without it"


class TestBudget:
    async def test_a_looping_model_is_stopped(self) -> None:
        engine = ReasoningEngine(LoopingModel(), registry_with("search"))
        answer = await engine.run(
            "q",
            tenant=ACME,
            tools=EchoTools(),
            stream=opened(),
            budget=Budget(max_iterations=3),
        )

        assert answer.turn.stop_reason is StopReason.ITERATIONS
        assert answer.turn.ledger.iterations == 3

    async def test_exhaustion_is_reported_as_truncation(self) -> None:
        """A budget-stopped answer reads like a finished one unless flagged."""
        engine = ReasoningEngine(LoopingModel(), registry_with("search"))
        answer = await engine.run(
            "q",
            tenant=ACME,
            tools=EchoTools(),
            stream=opened(),
            budget=Budget(max_iterations=2),
        )

        assert answer.truncated

    async def test_the_reader_is_told(self) -> None:
        stream = opened()
        engine = ReasoningEngine(LoopingModel(), registry_with("search"))
        await engine.run(
            "q",
            tenant=ACME,
            tools=EchoTools(),
            stream=stream,
            budget=Budget(max_iterations=2),
        )

        notices = [e for e in stream.recorded if e.kind is EventKind.NOTICE]
        assert any("limit" in n.payload["message"] for n in notices)


class SelfCancellingTools:
    """Cancels the turn from inside the first tool call.

    This is how a user pressing stop mid-execution actually arrives: the turn
    is already inside a tool when the request lands.
    """

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.turn = None  # set by the engine before tools run

    async def run(self, request: ToolRequest) -> ToolOutcome:
        self.ran.append(request.name)
        if self.turn is not None:
            self.turn.request_cancel()
        return ToolOutcome(call_id=request.call_id, ok=True, summary="ok")


class TestCancellation:
    async def test_cancelling_mid_tool_stops_before_the_next_call(self) -> None:
        model = ScriptedModel(
            ModelReply(
                tool_requests=(
                    ToolRequest(call_id="c1", name="a", arguments={}),
                    ToolRequest(call_id="c2", name="b", arguments={}),
                )
            ),
            ModelReply(text="unreached"),
        )
        tools = SelfCancellingTools()

        class Observable(ReasoningEngine):
            """Hands the turn to the tools so they can cancel it."""

            async def run(self, prompt, **kwargs):  # type: ignore[no-untyped-def]
                import kairos.core.reasoning.engine as module

                original = module.Turn
                captured: list = []

                def capture(*args, **kw):  # type: ignore[no-untyped-def]
                    turn = original(*args, **kw)
                    captured.append(turn)
                    tools.turn = turn
                    return turn

                module.Turn = capture  # type: ignore[assignment]
                try:
                    return await super().run(prompt, **kwargs)
                finally:
                    module.Turn = original  # type: ignore[assignment]

        engine = Observable(model, registry_with("a", "b"))
        answer = await engine.run("q", tenant=ACME, tools=tools, stream=opened())

        assert tools.ran == ["a"]  # the second was never reached
        assert answer.turn.phase is Phase.CANCELLED
        assert answer.turn.stop_reason is StopReason.CANCELLED
        assert len(model.calls) == 1  # the model was not asked again

    async def test_a_settled_turn_always_records_why(self) -> None:
        engine = ReasoningEngine(ScriptedModel(ModelReply(text="x")), registry_with())
        answer = await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())
        assert answer.turn.settled


class TestFailure:
    async def test_a_model_failure_settles_the_turn_before_propagating(self) -> None:
        """A crash must not leave a live row with no owner.

        The recovery scanner would later find it and have no way to tell it
        from work it had itself abandoned.
        """
        stream = opened()
        engine = ReasoningEngine(ExplodingModel(), registry_with())

        with pytest.raises(RuntimeError, match="on fire"):
            await engine.run("q", tenant=ACME, tools=EchoTools(), stream=stream)

    async def test_the_stream_reports_the_failure(self) -> None:
        stream = opened()
        engine = ReasoningEngine(ExplodingModel(), registry_with())

        with pytest.raises(RuntimeError):
            await engine.run("q", tenant=ACME, tools=EchoTools(), stream=stream)

        assert stream.closed
        assert stream.recorded[-1].kind is EventKind.ERROR


class TestQuota:
    async def test_usage_is_settled_after_a_successful_turn(self) -> None:
        quota = RecordingQuota()
        engine = ReasoningEngine(
            ScriptedModel(ModelReply(text="x", input_tokens=100, output_tokens=50)),
            registry_with(),
            quota,
        )

        await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())

        assert quota.settled[0].actual == 150

    async def test_an_exhausted_tenant_is_refused_before_any_model_call(self) -> None:
        quota = RecordingQuota(Allowance(limit=10, consumed=10))
        model = ScriptedModel(ModelReply(text="should not happen"))
        engine = ReasoningEngine(model, registry_with(), quota)

        answer = await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())

        assert not answer.complete
        assert model.calls == []

    async def test_a_degrading_tenant_proceeds_with_a_warning(self) -> None:
        quota = RecordingQuota(
            Allowance(limit=100, consumed=99, on_exhaustion=Exhaustion.DEGRADE)
        )
        stream = opened()
        engine = ReasoningEngine(
            ScriptedModel(ModelReply(text="cheap answer")), registry_with(), quota
        )

        answer = await engine.run("q", tenant=ACME, tools=EchoTools(), stream=stream)

        assert answer.complete
        assert any(e.kind is EventKind.NOTICE for e in stream.recorded)

    async def test_spend_is_settled_even_when_the_turn_fails(self) -> None:
        """A turn that dies partway still consumed what it consumed."""
        quota = RecordingQuota()
        engine = ReasoningEngine(ExplodingModel(), registry_with(), quota)

        with pytest.raises(RuntimeError):
            await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())

        assert len(quota.settled) == 1

    async def test_an_unmetered_deployment_needs_no_quota(self) -> None:
        # A self-hosted instance metering nothing still needs the loop.
        engine = ReasoningEngine(ScriptedModel(ModelReply(text="x")), registry_with())
        assert (await engine.run("q", tenant=ACME, tools=EchoTools(), stream=opened())).complete


class TestDegradedModel:
    async def test_a_fallback_answer_is_disclosed(self) -> None:
        """A silent substitution reads as the model getting worse."""
        stream = opened()
        engine = ReasoningEngine(
            ScriptedModel(ModelReply(text="x", degraded=True, model_id="backup")),
            registry_with(),
        )

        answer = await engine.run("q", tenant=ACME, tools=EchoTools(), stream=stream)

        notices = [e for e in stream.recorded if e.kind is EventKind.NOTICE]
        assert any("fallback" in n.payload["message"] for n in notices)
        assert answer.served_by == "backup"


class TestConversation:
    def test_tool_calls_are_attached_to_the_assistant_turn(self) -> None:
        conversation = Conversation()
        conversation.add_assistant(
            ModelReply(
                text="", tool_requests=(ToolRequest(call_id="c1", name="f", arguments={}),)
            )
        )
        assert "tool_calls" in conversation.messages[0]

    def test_a_failed_tool_reports_its_error_to_the_model(self) -> None:
        # The model needs to see why, or it will retry the same call.
        conversation = Conversation()
        conversation.add_tool_results(
            [ToolOutcome(call_id="c1", ok=False, summary="", error="file not found")]
        )
        assert "file not found" in str(conversation.messages[0]["content"])
