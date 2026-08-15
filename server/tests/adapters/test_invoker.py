"""Retry, fallback and breaker interaction — the orchestration, without a network."""

from __future__ import annotations

import pytest

from kairos.adapters.llm.credentials import (
    CredentialResolver,
    EnvironmentKeys,
    InMemoryKeyStore,
)
from kairos.adapters.llm.invoker import (
    ModelInvoker,
    Response,
    RetryPolicy,
    TransportFailure,
    crosses_providers,
    fallback_chain,
)
from kairos.adapters.llm.wire import Message, Request, Speaker, Turn
from kairos.core.catalog import (
    Capability,
    Catalog,
    Endpoint,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    TokenBudget,
    Wire,
)
from kairos.core.resilience.breaker import BreakerKey, BreakerPolicy, CircuitBreaker
from kairos.core.tenancy import TenantId

ACME = TenantId("acme")


def provider(pid: str, env: str = "KEY") -> ProviderDescriptor:
    return ProviderDescriptor(
        id=ProviderId(pid),
        display_name=pid,
        endpoint=Endpoint(wire=Wire.OPENAI_CHAT, credential_env=env),
    )


def model(mid: str, pid: str) -> ModelDescriptor:
    return ModelDescriptor(
        id=ModelId(mid),
        remote_id=mid,
        provider=ProviderId(pid),
        budget=TokenBudget(context=100_000, max_output=4_000),
        capabilities=Capability.baseline(),
    )


CATALOG = Catalog(
    providers=[provider("alpha"), provider("beta", env="BETA_KEY")],
    models=[model("primary", "alpha"), model("secondary", "beta")],
)

TURN = Turn(messages=[Message(speaker=Speaker.USER, text="Hello")])


class ScriptedTransport:
    """Replays a fixed sequence of outcomes, recording what it was asked."""

    def __init__(self, *outcomes: Response | Exception) -> None:
        self._outcomes = list(outcomes)
        self.sent: list[Request] = []

    async def send(self, request: Request) -> Response:
        self.sent.append(request)
        outcome = self._outcomes.pop(0) if self._outcomes else Response(200, {})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok(**usage: int) -> Response:
    return Response(200, {"usage": usage} if usage else {})


def failure(status: int, message: str = "boom") -> Response:
    return Response(status, {"error": {"message": message}})


async def nap(_seconds: float) -> None:
    """Backoff without the wait."""


def invoker(
    transport: ScriptedTransport,
    *,
    breaker: CircuitBreaker | None = None,
    retry: RetryPolicy | None = None,
    keys: dict[str, str] | None = None,
) -> ModelInvoker:
    return ModelInvoker(
        CATALOG,
        CredentialResolver(
            CATALOG,
            InMemoryKeyStore(),
            EnvironmentKeys(keys if keys is not None else {"KEY": "k", "BETA_KEY": "k2"}),
        ),
        breaker or CircuitBreaker(),
        transport,
        retry or RetryPolicy(max_attempts=2),
        sleep=nap,
    )


class TestSuccess:
    async def test_a_working_provider_is_called_once(self) -> None:
        transport = ScriptedTransport(ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert outcome.ok
        assert len(transport.sent) == 1
        assert outcome.served_by == "primary"

    async def test_the_first_choice_is_not_a_degradation(self) -> None:
        outcome = await invoker(ScriptedTransport(ok())).invoke(TURN, ACME, ["primary"])
        assert not outcome.degraded


class TestRetry:
    async def test_a_transient_failure_is_retried(self) -> None:
        transport = ScriptedTransport(failure(503), ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert outcome.ok
        assert len(transport.sent) == 2

    async def test_a_rejection_is_not_retried(self) -> None:
        """Sending the same rejected body again gets the same rejection."""
        transport = ScriptedTransport(failure(400, "malformed"))
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert not outcome.ok
        assert len(transport.sent) == 1

    async def test_a_transport_failure_is_retried(self) -> None:
        # No status at all — a reset or a timeout, the cheapest kind to recover
        # from.
        transport = ScriptedTransport(TransportFailure("connection reset"), ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert outcome.ok
        assert len(transport.sent) == 2

    async def test_retries_are_bounded(self) -> None:
        """Two attempts, not the six that layered retries produced.

        The reference implementation retried three times over an SDK that
        retried five, so one wobble became twenty-four requests against a
        provider that was already struggling.
        """
        transport = ScriptedTransport(failure(503), failure(503), failure(503))
        await invoker(transport, retry=RetryPolicy(max_attempts=2)).invoke(
            TURN, ACME, ["primary"]
        )
        assert len(transport.sent) == 2


class TestFallback:
    async def test_it_moves_on_when_a_provider_does_not_recover(self) -> None:
        transport = ScriptedTransport(failure(503), failure(503), ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary", "secondary"])

        assert outcome.ok
        assert outcome.served_by == "secondary"
        assert outcome.degraded

    async def test_a_rejection_moves_on_immediately(self) -> None:
        transport = ScriptedTransport(failure(400), ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary", "secondary"])

        assert outcome.served_by == "secondary"
        assert len(transport.sent) == 2  # no retry on the first

    async def test_exhausting_the_chain_is_reported(self) -> None:
        transport = ScriptedTransport(*[failure(503)] * 4)
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary", "secondary"])

        assert not outcome.ok
        assert outcome.exhausted

    async def test_an_empty_chain_is_exhausted_without_calling_anything(self) -> None:
        transport = ScriptedTransport()
        outcome = await invoker(transport).invoke(TURN, ACME, [])

        assert outcome.exhausted
        assert not transport.sent


class TestBreakerInteraction:
    async def test_an_open_provider_is_skipped(self) -> None:
        """Skipping costs nothing; trying costs the full retry budget first."""
        breaker = CircuitBreaker(BreakerPolicy(failure_threshold=1))
        breaker.record_failure(BreakerKey(ACME, ProviderId("alpha")))

        transport = ScriptedTransport(ok())
        outcome = await invoker(transport, breaker=breaker).invoke(
            TURN, ACME, ["primary", "secondary"]
        )

        assert outcome.served_by == "secondary"
        assert len(transport.sent) == 1

    async def test_failures_trip_the_breaker(self) -> None:
        breaker = CircuitBreaker(BreakerPolicy(failure_threshold=2))
        transport = ScriptedTransport(failure(503), failure(503))
        await invoker(transport, breaker=breaker).invoke(TURN, ACME, ["primary"])

        assert breaker.is_open(BreakerKey(ACME, ProviderId("alpha")))

    async def test_a_missing_credential_does_not_trip_the_breaker(self) -> None:
        """The provider is fine; this deployment has no way in.

        Tripping here would keep a healthy provider closed long after the key
        was supplied.
        """
        breaker = CircuitBreaker(BreakerPolicy(failure_threshold=1))
        transport = ScriptedTransport(ok())
        outcome = await invoker(transport, breaker=breaker, keys={}).invoke(
            TURN, ACME, ["primary"]
        )

        assert not outcome.ok
        assert not breaker.is_open(BreakerKey(ACME, ProviderId("alpha")))

    async def test_one_tenants_failures_do_not_close_another_out(self) -> None:
        breaker = CircuitBreaker(BreakerPolicy(failure_threshold=1))
        subject = invoker(ScriptedTransport(*[failure(503)] * 6), breaker=breaker)

        await subject.invoke(TURN, TenantId("noisy"), ["primary"])
        assert breaker.is_open(BreakerKey(TenantId("noisy"), ProviderId("alpha")))
        assert not breaker.is_open(BreakerKey(TenantId("quiet"), ProviderId("alpha")))


class TestUsageAccounting:
    async def test_usage_from_failed_attempts_still_counts(self) -> None:
        """A failed attempt can still have consumed tokens upstream.

        Counting only the successful one would understate what the tenant
        actually cost.
        """
        transport = ScriptedTransport(
            Response(503, {"usage": {"prompt_tokens": 100, "completion_tokens": 0}}),
            ok(prompt_tokens=100, completion_tokens=50),
        )
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert outcome.usage.input_tokens == 200
        assert outcome.usage.output_tokens == 50

    async def test_every_attempt_is_recorded(self) -> None:
        transport = ScriptedTransport(failure(503), failure(503), ok())
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary", "secondary"])

        assert len(outcome.attempts) == 3
        assert [a.provider_id for a in outcome.attempts] == ["alpha", "alpha", "beta"]

    async def test_the_failure_description_prefers_the_providers_own_message(
        self,
    ) -> None:
        transport = ScriptedTransport(failure(429, "rate limit exceeded"))
        outcome = await invoker(transport).invoke(TURN, ACME, ["primary"])

        assert "rate limit exceeded" in outcome.attempts[0].error


class TestBackoff:
    def test_the_first_attempt_is_immediate(self) -> None:
        assert RetryPolicy().backoff(1) == 0.0

    def test_delays_grow(self) -> None:
        policy = RetryPolicy(initial_backoff=1.0, multiplier=2.0, jitter=0.0)
        assert policy.backoff(2) == 1.0
        assert policy.backoff(3) == 2.0

    def test_delays_are_capped(self) -> None:
        policy = RetryPolicy(initial_backoff=1.0, max_backoff=3.0, jitter=0.0)
        assert policy.backoff(10) == 3.0

    def test_jitter_spreads_retries(self) -> None:
        """So that a fleet recovering from one outage does not cause another."""
        import random as _random

        policy = RetryPolicy(initial_backoff=10.0, jitter=0.25)
        delays = {policy.backoff(2, rng=_random.Random(seed)) for seed in range(20)}
        assert len(delays) > 1

    def test_a_degenerate_policy_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)


class TestChainConstruction:
    def test_unknown_models_are_dropped(self) -> None:
        """A chain naming a removed model would fail like an outage."""
        assert fallback_chain(CATALOG, "primary", "ghost", "secondary") == (
            "primary",
            "secondary",
        )

    def test_a_single_provider_chain_is_flagged(self) -> None:
        # Every entry fails together, so it does not survive that vendor
        # having a bad afternoon.
        assert not crosses_providers(CATALOG, ["primary"])

    def test_a_cross_provider_chain_is_recognised(self) -> None:
        assert crosses_providers(CATALOG, ["primary", "secondary"])
