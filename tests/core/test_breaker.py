"""Failure memory, and the tenant isolation that makes it safe."""

from __future__ import annotations

import pytest

from kairos.core.catalog import ProviderId
from kairos.core.resilience.breaker import (
    BreakerKey,
    BreakerPolicy,
    CircuitBreaker,
    State,
    plan_fallbacks,
)
from kairos.core.tenancy import TenantId


class FakeClock:
    """A clock tests can advance, so recovery windows need no sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def key(tenant: str = "acme", provider: str = "vendor") -> BreakerKey:
    return BreakerKey(TenantId(tenant), ProviderId(provider))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(
        BreakerPolicy(failure_threshold=3, recovery_after_seconds=30, success_threshold=2),
        clock=clock,
    )


class TestOpening:
    def test_starts_closed(self, breaker: CircuitBreaker) -> None:
        assert breaker.allows(key())
        assert breaker.state_of(key()) is State.CLOSED

    def test_opens_at_the_threshold(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        assert breaker.is_open(key())
        assert not breaker.allows(key())

    def test_stays_closed_below_the_threshold(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure(key())
        breaker.record_failure(key())
        assert breaker.allows(key())

    def test_a_success_clears_accumulated_failures(self, breaker: CircuitBreaker) -> None:
        """The threshold counts consecutive failures, not lifetime ones.

        Otherwise a provider with a low but non-zero error rate would trip
        eventually no matter how healthy it is.
        """
        breaker.record_failure(key())
        breaker.record_failure(key())
        breaker.record_success(key())
        breaker.record_failure(key())
        breaker.record_failure(key())
        assert breaker.allows(key())


class TestRecovery:
    def test_stays_open_before_the_window_elapses(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(29)
        assert not breaker.allows(key())

    def test_admits_a_probe_once_the_window_elapses(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(30)
        assert breaker.allows(key())
        assert breaker.state_of(key()) is State.HALF_OPEN

    def test_only_one_probe_is_admitted_at_a_time(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        """Without this cap, every waiting request probes at once.

        That stampede against a recovering provider is precisely what the
        breaker exists to prevent.
        """
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(30)

        assert breaker.allows(key())
        assert not breaker.allows(key())
        assert not breaker.allows(key())

    def test_closes_after_enough_successful_probes(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(30)

        breaker.allows(key())
        breaker.record_success(key())
        breaker.allows(key())
        breaker.record_success(key())

        assert breaker.state_of(key()) is State.CLOSED
        assert breaker.allows(key())

    def test_a_failed_probe_reopens_with_a_fresh_timer(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(30)
        breaker.allows(key())

        breaker.record_failure(key())

        assert breaker.is_open(key())
        clock.advance(29)
        assert not breaker.allows(key())
        clock.advance(1)
        assert breaker.allows(key())


class TestTenantIsolation:
    def test_one_tenant_cannot_trip_another(self, breaker: CircuitBreaker) -> None:
        """The reason the key includes the tenant.

        A global breaker would let one tenant's exhausted quota or revoked
        credentials cut every other tenant off from the same provider — the
        noisy neighbour problem arriving through the failure path.
        """
        for _ in range(3):
            breaker.record_failure(key("noisy", "vendor"))

        assert breaker.is_open(key("noisy", "vendor"))
        assert breaker.allows(key("quiet", "vendor"))

    def test_one_provider_failing_does_not_block_the_others(
        self, breaker: CircuitBreaker
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key("acme", "broken"))

        assert breaker.is_open(key("acme", "broken"))
        assert breaker.allows(key("acme", "healthy"))

    def test_a_real_outage_trips_per_tenant_independently(
        self, breaker: CircuitBreaker
    ) -> None:
        # Each tenant discovers the outage once and then stops paying for it.
        for tenant in ("alpha", "beta"):
            for _ in range(3):
                breaker.record_failure(key(tenant, "down"))

        assert breaker.is_open(key("alpha", "down"))
        assert breaker.is_open(key("beta", "down"))
        assert breaker.allows(key("gamma", "down"))


class TestFallbackPlanning:
    def test_open_providers_are_skipped(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            breaker.record_failure(key("acme", "first"))

        plan = plan_fallbacks(
            breaker,
            TenantId("acme"),
            (ProviderId("first"), ProviderId("second"), ProviderId("third")),
        )

        assert plan.candidates == ("second", "third")
        assert plan.skipped == ("first",)

    def test_a_healthy_chain_is_untouched(self, breaker: CircuitBreaker) -> None:
        chain = (ProviderId("a"), ProviderId("b"))
        plan = plan_fallbacks(breaker, TenantId("acme"), chain)
        assert plan.candidates == chain
        assert not plan.exhausted

    def test_everything_open_still_tries_the_first(self, breaker: CircuitBreaker) -> None:
        """Refusing to call anything turns degradation into an outage.

        One request against a maybe-recovering provider is cheaper than
        telling the tenant no.
        """
        for provider in ("a", "b"):
            for _ in range(3):
                breaker.record_failure(key("acme", provider))

        plan = plan_fallbacks(breaker, TenantId("acme"), (ProviderId("a"), ProviderId("b")))

        assert plan.candidates == ("a",)
        assert not plan.exhausted

    def test_an_empty_chain_is_exhausted(self, breaker: CircuitBreaker) -> None:
        assert plan_fallbacks(breaker, TenantId("acme"), ()).exhausted


class TestAdministration:
    def test_reset_forgets_a_circuit(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        breaker.reset(key())
        assert breaker.allows(key())

    def test_open_circuits_are_reportable(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            breaker.record_failure(key("acme", "broken"))
        breaker.record_failure(key("acme", "flaky"))

        assert breaker.open_circuits() == (key("acme", "broken"),)

    def test_recovered_circuits_drop_off_the_report(
        self, breaker: CircuitBreaker, clock: FakeClock
    ) -> None:
        for _ in range(3):
            breaker.record_failure(key())
        clock.advance(30)
        assert breaker.open_circuits() == ()


class TestPolicyValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"failure_threshold": 0},
            {"success_threshold": 0},
            {"recovery_after_seconds": 0},
            {"half_open_max_calls": 0},
        ],
    )
    def test_degenerate_policies_are_rejected(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            BreakerPolicy(**kwargs)  # type: ignore[arg-type]
