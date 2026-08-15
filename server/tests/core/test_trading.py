"""Proposals, orders read back, and the envelope around them.

The envelope's job is to refuse while someone is still looking at the form. So
the cases that matter are the ones where a refusal is easy to get subtly wrong:
limits measured before the fill rather than after, a market order treated as
free because it has no price, and a bypass that leaves no trace.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kairos.core.tenancy.context import TenantId
from kairos.core.trading import (
    AccountState,
    OrderStatus,
    OrderView,
    Position,
    RiskLimits,
    RiskRefusal,
    Side,
    TradeProposal,
    assess,
)

TENANT = TenantId("acme")


def proposal(**overrides: object) -> TradeProposal:
    base: dict[str, object] = {
        "tenant_id": TENANT,
        "account_id": "paper",
        "symbol": "NVDA",
        "side": Side.BUY,
        "quantity": 10,
        "limit_price": Decimal("100"),
    }
    return TradeProposal(**(base | overrides))  # type: ignore[arg-type]


def account(**overrides: object) -> AccountState:
    base: dict[str, object] = {"equity": Decimal("100000"), "positions": {}, "orders_today": 0}
    return AccountState(**(base | overrides))  # type: ignore[arg-type]


class TestProposal:
    def test_it_carries_its_own_idempotency_key(self) -> None:
        """Generated here, so a retried submission collapses into the original.

        If the worker generated it, a retry would look like a second order.
        """
        assert proposal().proposal_id != proposal().proposal_id

    def test_a_missing_limit_means_market(self) -> None:
        assert proposal(limit_price=None).is_market_order

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_quantity_must_be_positive(self, quantity: int) -> None:
        with pytest.raises(ValueError, match="quantity"):
            proposal(quantity=quantity)

    def test_a_symbol_is_required(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            proposal(symbol="   ")

    def test_a_non_positive_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="limit price"):
            proposal(limit_price=Decimal("0"))

    def test_money_crosses_the_wire_as_text(self) -> None:
        """A float would round the price where nobody could see it happen."""
        wire = proposal(limit_price=Decimal("123.456")).to_wire()
        assert wire["limitPrice"] == "123.456"

    def test_a_market_order_sends_a_null_price(self) -> None:
        assert proposal(limit_price=None).to_wire()["limitPrice"] is None

    def test_the_wire_uses_the_workers_field_names(self) -> None:
        wire = proposal().to_wire()
        assert {"proposalId", "tenantId", "accountId", "action"} <= set(wire)
        assert wire["action"] == "BUY"


class TestOrderStatus:
    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.DENIED,
        ],
    )
    def test_terminal_statuses(self, status: OrderStatus) -> None:
        assert status.terminal

    def test_an_unsubmitted_order_is_neither_working_nor_finished(self) -> None:
        """Showing it as live would suggest the broker already has it."""
        assert not OrderStatus.INITIALIZED.terminal
        assert not OrderStatus.INITIALIZED.working

    def test_a_partial_fill_is_still_working(self) -> None:
        assert OrderStatus.PARTIALLY_FILLED.working


class TestOrderView:
    def test_remaining_is_what_is_left(self) -> None:
        order = OrderView(
            broker_order_id="1",
            proposal_id="p",
            symbol="NVDA",
            side=Side.BUY,
            quantity=10,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=4,
        )
        assert order.remaining == 6

    def test_an_overfill_does_not_go_negative(self) -> None:
        """A broker can report more than was asked for.

        Rendered as a negative remainder it reads as a bug rather than as the
        unusual event it is.
        """
        order = OrderView(
            broker_order_id="1",
            proposal_id="p",
            symbol="NVDA",
            side=Side.BUY,
            quantity=10,
            status=OrderStatus.FILLED,
            filled_quantity=12,
        )
        assert order.remaining == 0

    def test_notional_is_what_actually_changed_hands(self) -> None:
        order = OrderView(
            broker_order_id="1",
            proposal_id="p",
            symbol="NVDA",
            side=Side.BUY,
            quantity=10,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=4,
            average_price=Decimal("101.25"),
        )
        assert order.notional == Decimal("405.00")


class TestLimitsConfiguration:
    def test_an_order_limit_above_the_position_limit_is_refused(self) -> None:
        """Otherwise one order could open a position the position limit forbids.

        Which of the two applied would then depend on whether anything was held
        already, which is not a rule anyone could reason about.
        """
        with pytest.raises(ValueError, match="max_order_fraction"):
            RiskLimits(
                max_order_fraction=Decimal("0.5"), max_position_fraction=Decimal("0.3")
            )

    @pytest.mark.parametrize("fraction", [Decimal("0"), Decimal("1.5")])
    def test_a_fraction_outside_zero_to_one_is_refused(self, fraction: Decimal) -> None:
        with pytest.raises(ValueError):
            RiskLimits(max_order_fraction=fraction)


class TestOrderSize:
    def test_a_small_order_passes(self) -> None:
        # 10 × 100 = 1,000 against 100,000 equity — 1%, inside the 2% limit.
        assert assess(proposal(), account(), RiskLimits()).allowed

    def test_an_oversized_order_is_refused(self) -> None:
        decision = assess(proposal(quantity=100), account(), RiskLimits())
        assert RiskRefusal.ORDER_TOO_LARGE in decision.refusals

    def test_the_refusal_says_by_how_much(self) -> None:
        """A refusal that does not say what the limit was cannot be acted on."""
        decision = assess(proposal(quantity=100), account(), RiskLimits())
        assert any("10.0% of equity" in d for d in decision.detail)
        assert any("2.0%" in d for d in decision.detail)


class TestPositionSize:
    def test_the_limit_applies_after_the_proposed_fill(self) -> None:
        """Checking the position before the order would let a series of
        individually-acceptable orders accumulate past the limit."""
        held = {"NVDA": Position("NVDA", quantity=295, average_cost=Decimal("100"))}
        decision = assess(
            proposal(quantity=10),
            account(positions=held),
            RiskLimits(max_order_fraction=Decimal("0.02")),
        )
        assert RiskRefusal.POSITION_TOO_LARGE in decision.refusals

    def test_a_sale_is_not_measured_against_the_position_limit(self) -> None:
        """Selling reduces exposure; refusing it for being large is backwards."""
        held = {"NVDA": Position("NVDA", quantity=400, average_cost=Decimal("100"))}
        decision = assess(
            proposal(side=Side.SELL, quantity=10), account(positions=held), RiskLimits()
        )
        assert RiskRefusal.POSITION_TOO_LARGE not in decision.refusals


class TestShortSelling:
    def test_selling_more_than_is_held_is_refused(self) -> None:
        """Short selling is not modelled.

        Letting it through as an ordinary sale would open a position the
        position limit was never measured against.
        """
        held = {"NVDA": Position("NVDA", quantity=5, average_cost=Decimal("100"))}
        decision = assess(
            proposal(side=Side.SELL, quantity=10), account(positions=held), RiskLimits()
        )
        assert RiskRefusal.SELLING_WHAT_IS_NOT_HELD in decision.refusals

    def test_selling_exactly_what_is_held_is_allowed(self) -> None:
        held = {"NVDA": Position("NVDA", quantity=10, average_cost=Decimal("100"))}
        decision = assess(
            proposal(side=Side.SELL, quantity=10), account(positions=held), RiskLimits()
        )
        assert decision.allowed


class TestDailyCap:
    def test_the_cap_is_enforced(self) -> None:
        decision = assess(proposal(), account(orders_today=3), RiskLimits())
        assert RiskRefusal.DAILY_LIMIT_REACHED in decision.refusals

    def test_one_below_the_cap_still_passes(self) -> None:
        assert assess(proposal(), account(orders_today=2), RiskLimits()).allowed


class TestUniverse:
    def test_a_symbol_outside_the_universe_is_refused(self) -> None:
        limits = RiskLimits(universe=frozenset({"AAPL"}))
        decision = assess(proposal(symbol="NVDA"), account(), limits)
        assert RiskRefusal.NOT_IN_UNIVERSE in decision.refusals

    def test_an_empty_universe_means_no_restriction(self) -> None:
        """Rather than "nothing is tradable", which would refuse everything on
        a deployment that simply had not configured one."""
        assert assess(proposal(), account(), RiskLimits(universe=frozenset())).allowed


class TestPricing:
    def test_a_market_order_needs_a_reference_price(self) -> None:
        """Treating an unpriced order as free would let it past every check."""
        with pytest.raises(ValueError, match="reference price"):
            assess(proposal(limit_price=None), account(), RiskLimits())

    def test_a_reference_price_is_used_when_given(self) -> None:
        decision = assess(
            proposal(limit_price=None, quantity=100),
            account(),
            RiskLimits(),
            reference_price=Decimal("100"),
        )
        assert RiskRefusal.ORDER_TOO_LARGE in decision.refusals

    def test_an_account_with_no_equity_cannot_size_anything(self) -> None:
        decision = assess(proposal(), account(equity=Decimal("0")), RiskLimits())
        assert RiskRefusal.NO_EQUITY in decision.refusals


class TestMultipleBreaches:
    def test_every_breach_is_reported(self) -> None:
        """Reporting only the first turns fixing it into a guessing game where
        each correction reveals the next objection."""
        decision = assess(
            proposal(symbol="TSLA", quantity=100),
            account(orders_today=5),
            RiskLimits(universe=frozenset({"NVDA"})),
        )
        assert {
            RiskRefusal.NOT_IN_UNIVERSE,
            RiskRefusal.DAILY_LIMIT_REACHED,
            RiskRefusal.ORDER_TOO_LARGE,
        } <= set(decision.refusals)


class TestBypass:
    def test_it_requires_a_written_reason(self) -> None:
        """Using it should leave a trace rather than look like a pass."""
        with pytest.raises(ValueError, match="written reason"):
            assess(proposal(quantity=100), account(), RiskLimits(), bypass=True)

    def test_it_is_recorded_on_the_decision(self) -> None:
        decision = assess(
            proposal(quantity=100),
            account(),
            RiskLimits(),
            bypass=True,
            bypass_reason="manual unwind approved by desk",
        )
        assert decision.allowed
        assert decision.bypassed
        assert "manual unwind" in decision.detail[0]
