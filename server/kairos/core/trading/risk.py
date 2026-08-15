"""What may be proposed at all.

The worker refuses bad orders too, and its refusal is the one that counts —
it is closest to the broker and it is what runs when a proposal arrives from
somewhere else. This layer exists for a different reason: a refusal here
happens while a person is still looking at the form, and it can say which limit
was hit and by how much. A refusal from the worker arrives as a `DENIED` status
several seconds later, attached to an order nobody wanted.

Every limit is expressed against the account's equity rather than as an
absolute sum, so that the envelope scales with the account instead of having to
be re-tuned whenever it grows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from kairos.core.trading.orders import Position, Side, TradeProposal


class RiskRefusal(StrEnum):
    """Why a proposal was refused.

    An enum rather than a message so the interface can react differently to
    each: an unknown symbol is a mistake to correct, a daily cap is a wait, and
    an oversized position is a decision to reconsider.
    """

    NOT_IN_UNIVERSE = "not_in_universe"
    ORDER_TOO_LARGE = "order_too_large"
    POSITION_TOO_LARGE = "position_too_large"
    DAILY_LIMIT_REACHED = "daily_limit_reached"
    NO_EQUITY = "no_equity"
    SELLING_WHAT_IS_NOT_HELD = "selling_what_is_not_held"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """The envelope.

    Defaults are deliberately tight. An envelope that has to be loosened
    on purpose leaves a trace of someone deciding to; one that starts loose
    leaves none.
    """

    max_order_fraction: Decimal = Decimal("0.02")
    max_position_fraction: Decimal = Decimal("0.30")
    max_orders_per_day: int = 3
    universe: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not (0 < self.max_order_fraction <= 1):
            raise ValueError("max_order_fraction must be a fraction of equity")
        if not (0 < self.max_position_fraction <= 1):
            raise ValueError("max_position_fraction must be a fraction of equity")
        if self.max_orders_per_day < 0:
            raise ValueError("max_orders_per_day cannot be negative")
        if self.max_order_fraction > self.max_position_fraction:
            # Otherwise a single order could open a position the position limit
            # forbids, and which of the two applies would depend on whether
            # anything was held already.
            raise ValueError(
                "max_order_fraction exceeds max_position_fraction; a single "
                "order could open a position the position limit forbids"
            )


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Allowed, or refused with reasons.

    Reasons plural: a proposal can breach more than one limit, and reporting
    only the first turns fixing it into a guessing game where each correction
    reveals the next objection.
    """

    allowed: bool
    refusals: tuple[RiskRefusal, ...] = ()
    detail: tuple[str, ...] = ()
    bypassed: bool = False

    @property
    def refused(self) -> bool:
        return not self.allowed


@dataclass(frozen=True, slots=True)
class AccountState:
    """What the envelope is measured against."""

    equity: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    orders_today: int = 0


def assess(
    proposal: TradeProposal,
    account: AccountState,
    limits: RiskLimits,
    *,
    reference_price: Decimal | None = None,
    bypass: bool = False,
    bypass_reason: str = "",
) -> RiskDecision:
    """Decide whether a proposal may be sent.

    `reference_price` is what the order is valued at. For a limit order it is
    the limit; for a market order the caller must supply one, because an
    unpriced market order cannot be measured against any limit — and treating
    it as free would let it through every check.

    `bypass` exists because a person with authority sometimes has a reason the
    rules do not encode. It requires a written reason and is recorded on the
    decision, so that using it leaves a trace rather than looking like a
    proposal that simply passed.
    """
    if bypass:
        if not bypass_reason.strip():
            raise ValueError("bypassing the risk envelope requires a written reason")
        return RiskDecision(allowed=True, bypassed=True, detail=(bypass_reason,))

    refusals: list[RiskRefusal] = []
    detail: list[str] = []

    if limits.universe and proposal.symbol not in limits.universe:
        refusals.append(RiskRefusal.NOT_IN_UNIVERSE)
        detail.append(f"{proposal.symbol} is not in the tradable universe")

    if limits.max_orders_per_day and account.orders_today >= limits.max_orders_per_day:
        refusals.append(RiskRefusal.DAILY_LIMIT_REACHED)
        detail.append(
            f"{account.orders_today} orders already placed today, "
            f"limit is {limits.max_orders_per_day}"
        )

    held = account.positions.get(proposal.symbol)
    if proposal.side is Side.SELL:
        holding = held.quantity if held else 0
        if holding < proposal.quantity:
            # Short selling is not modelled. Letting it through as an ordinary
            # sell would open a position the position limit was never measured
            # against.
            refusals.append(RiskRefusal.SELLING_WHAT_IS_NOT_HELD)
            detail.append(
                f"holding {holding} {proposal.symbol}, proposing to sell "
                f"{proposal.quantity}"
            )

    price = reference_price if reference_price is not None else proposal.limit_price
    if price is None:
        raise ValueError(
            "a market order needs a reference price; without one it cannot be "
            "measured against any limit"
        )

    if account.equity <= 0:
        refusals.append(RiskRefusal.NO_EQUITY)
        detail.append("account has no equity to size against")
        return RiskDecision(allowed=False, refusals=tuple(refusals), detail=tuple(detail))

    order_value = price * proposal.quantity
    order_fraction = order_value / account.equity
    if order_fraction > limits.max_order_fraction:
        refusals.append(RiskRefusal.ORDER_TOO_LARGE)
        detail.append(
            f"order is {_percent(order_fraction)} of equity, "
            f"limit is {_percent(limits.max_order_fraction)}"
        )

    if proposal.side is Side.BUY:
        # Measured after the proposed fill, not before. Checking the current
        # position would allow a series of individually-acceptable orders to
        # accumulate past the limit.
        resulting = (held.quantity if held else 0) + proposal.quantity
        resulting_fraction = (price * resulting) / account.equity
        if resulting_fraction > limits.max_position_fraction:
            refusals.append(RiskRefusal.POSITION_TOO_LARGE)
            detail.append(
                f"position would become {_percent(resulting_fraction)} of equity, "
                f"limit is {_percent(limits.max_position_fraction)}"
            )

    return RiskDecision(
        allowed=not refusals,
        refusals=tuple(refusals),
        detail=tuple(detail),
    )


def _percent(fraction: Decimal) -> str:
    return f"{fraction * 100:.1f}%"
