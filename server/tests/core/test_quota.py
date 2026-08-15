"""Metering tokens rather than requests, and the two-phase protocol that needs."""

from __future__ import annotations

import pytest

from kairos.core.quota.reservation import (
    Allowance,
    Estimate,
    Exhaustion,
    QuotaPolicy,
    Reservation,
    Settlement,
    abandon,
    reserve,
    settle,
)
from kairos.core.tenancy import TenantId

ACME = TenantId("acme")


def allowance(
    limit: int = 1000,
    consumed: int = 0,
    reserved: int = 0,
    on_exhaustion: Exhaustion = Exhaustion.REJECT,
) -> Allowance:
    return Allowance(
        limit=limit, consumed=consumed, reserved=reserved, on_exhaustion=on_exhaustion
    )


def estimate(prompt: int = 100, output: int = 100, multiplier: float = 1.0) -> Estimate:
    return Estimate(
        prompt_tokens=prompt, max_output_tokens=output, multiplier=multiplier
    )


class TestAvailability:
    def test_reserved_tokens_count_against_the_allowance(self) -> None:
        """The property a post-hoc counter cannot provide.

        If in-flight work did not count, a burst of concurrent requests would
        each see a healthy balance and collectively overrun.
        """
        assert allowance(limit=1000, consumed=200, reserved=300).available == 500

    def test_availability_never_goes_negative(self) -> None:
        assert allowance(limit=100, consumed=150).available == 0

    def test_exhaustion_is_reported(self) -> None:
        assert allowance(limit=100, consumed=100).exhausted

    @pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": -1}, {"consumed": -1}])
    def test_degenerate_allowances_are_rejected(self, bad: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            allowance(**bad)


class TestEstimation:
    def test_the_estimate_is_the_worst_case(self) -> None:
        # Cost is unknown until the work finishes, so reserving anything less
        # than the maximum would let concurrent requests overrun.
        assert estimate(prompt=1000, output=4000).worst_case == 5000

    def test_a_multiplier_widens_the_estimate(self) -> None:
        assert estimate(prompt=1000, output=1000, multiplier=1.5).worst_case == 3000

    def test_a_multiplier_cannot_shrink_it(self) -> None:
        with pytest.raises(ValueError, match="cannot reduce"):
            estimate(multiplier=0.5)


class TestGranting:
    def test_affordable_work_is_granted(self) -> None:
        decision = QuotaPolicy().evaluate(ACME, allowance(), estimate())
        assert decision.granted
        assert decision.reservation is not None
        assert decision.reservation.tokens == 200

    def test_a_warning_precedes_exhaustion(self) -> None:
        decision = QuotaPolicy(warn_at_fraction=0.9).evaluate(
            ACME, allowance(limit=1000, consumed=850), estimate(prompt=50, output=50)
        )
        assert decision.granted
        assert decision.warnings

    def test_plenty_of_headroom_warns_about_nothing(self) -> None:
        decision = QuotaPolicy().evaluate(ACME, allowance(limit=10_000), estimate())
        assert not decision.warnings


class TestExhaustionPolicies:
    def test_reject_blocks(self) -> None:
        decision = QuotaPolicy().evaluate(
            ACME, allowance(limit=100, consumed=100), estimate()
        )
        assert decision.blocked
        assert "exhausted" in decision.reason

    def test_degrade_grants_what_remains(self) -> None:
        """Continuing on a cheaper model beats refusing outright.

        The caller is expected to honour the flag; the reservation is sized to
        what is actually left rather than to the estimate.
        """
        decision = QuotaPolicy().evaluate(
            ACME,
            allowance(limit=1000, consumed=950, on_exhaustion=Exhaustion.DEGRADE),
            estimate(prompt=100, output=100),
        )
        assert decision.granted
        assert decision.degrade
        assert decision.reservation is not None
        assert decision.reservation.tokens == 50
        assert decision.reservation.degraded

    def test_allow_grants_the_full_estimate_and_warns(self) -> None:
        decision = QuotaPolicy().evaluate(
            ACME,
            allowance(limit=100, consumed=100, on_exhaustion=Exhaustion.ALLOW),
            estimate(),
        )
        assert decision.granted
        assert not decision.degrade
        assert decision.warnings


class TestSettlement:
    def test_unspent_tokens_are_returned(self) -> None:
        held = Reservation.issue(ACME, 5000)
        after = settle(
            reserve(allowance(limit=10_000), held),
            Settlement(reservation=held, input_tokens=100, output_tokens=200),
        )
        assert after.consumed == 300
        assert after.reserved == 0
        assert after.available == 9700

    def test_an_overrun_is_charged_and_flagged(self) -> None:
        """Stopping a half-finished turn wastes what it already spent.

        So an overrun is permitted — but recorded, because a persistent one
        means the estimate is wrong.
        """
        held = Reservation.issue(ACME, 1000)
        settlement = Settlement(reservation=held, input_tokens=800, output_tokens=700)

        assert settlement.overran
        assert settlement.adjustment == -500

        after = settle(reserve(allowance(limit=10_000), held), settlement)
        assert after.consumed == 1500

    def test_settling_releases_only_its_own_reservation(self) -> None:
        # Two requests in flight; settling one must not free the other's hold.
        mine, theirs = Reservation.issue(ACME, 1000), Reservation.issue(ACME, 2000)
        start = reserve(reserve(allowance(limit=10_000), mine), theirs)
        assert start.reserved == 3000

        after = settle(
            start, Settlement(reservation=mine, input_tokens=100, output_tokens=100)
        )
        assert after.reserved == 2000


class TestAbandonment:
    def test_abandoning_frees_the_hold(self) -> None:
        """Every failure path needs this.

        A reservation neither settled nor abandoned holds part of the allowance
        until the period rolls over, so a run of crashes looks to the tenant
        like a quota that shrinks on its own.
        """
        held = Reservation.issue(ACME, 5000)
        after = abandon(reserve(allowance(limit=10_000), held), held)
        assert after.reserved == 0
        assert after.consumed == 0
        assert after.available == 10_000

    def test_abandoning_twice_does_not_go_negative(self) -> None:
        held = Reservation.issue(ACME, 500)
        once = abandon(reserve(allowance(), held), held)
        assert abandon(once, held).reserved == 0


class TestConcurrentPressure:
    def test_reservations_accumulate_until_the_allowance_is_gone(self) -> None:
        """Several requests starting at once cannot collectively overrun."""
        policy = QuotaPolicy()
        current = allowance(limit=1000)
        request = estimate(prompt=100, output=300)  # 400 apiece

        granted = 0
        for _ in range(5):
            decision = policy.evaluate(ACME, current, request)
            if decision.blocked:
                break
            assert decision.reservation is not None
            current = reserve(current, decision.reservation)
            granted += 1

        assert granted == 2
        assert current.reserved == 800

    def test_settling_frees_room_for_the_next(self) -> None:
        policy = QuotaPolicy()
        request = estimate(prompt=100, output=300)

        first = policy.evaluate(ACME, allowance(limit=1000), request)
        assert first.reservation is not None
        current = reserve(allowance(limit=1000), first.reservation)

        # The work turned out cheap, so the headroom comes back.
        current = settle(
            current,
            Settlement(reservation=first.reservation, input_tokens=50, output_tokens=50),
        )
        assert current.available == 900
        assert policy.evaluate(ACME, current, request).granted


class TestPolicyValidation:
    @pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
    def test_degenerate_warning_thresholds_are_rejected(self, fraction: float) -> None:
        with pytest.raises(ValueError):
            QuotaPolicy(warn_at_fraction=fraction)
