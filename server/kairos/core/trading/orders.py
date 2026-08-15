"""Proposals going out, orders coming back.

Two shapes and one port. `TradeProposal` is what this platform sends; the
worker owns everything that happens after. `OrderView` is what it reads back —
named a view rather than an order because it is a copy of someone else's
record, and treating it as authoritative is how a stale status ends up
overwriting a fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from kairos.core.tenancy.context import TenantId


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    """The worker's status vocabulary, mirrored.

    Mirrored rather than reinvented: these strings are what arrives in the
    order row, and translating them into a second vocabulary here would mean
    every reader has to know both. `DENIED` is the risk refusal — an order that
    never reached the broker at all, which is a different thing from one the
    broker rejected.
    """

    INITIALIZED = "INITIALIZED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PENDING_UPDATE = "PENDING_UPDATE"
    PENDING_CANCEL = "PENDING_CANCEL"
    TRIGGERED = "TRIGGERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    DENIED = "DENIED"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def working(self) -> bool:
        """Still capable of filling.

        Not simply `not terminal`: an order that has not yet been submitted is
        neither working nor finished, and showing it as live would suggest the
        broker has it.
        """
        return self in _WORKING


_TERMINAL = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    }
)

_WORKING = frozenset(
    {
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.TRIGGERED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.PENDING_UPDATE,
        OrderStatus.PENDING_CANCEL,
    }
)


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """What the platform asks the worker to do.

    `proposal_id` is generated here and is half of the idempotency key — the
    worker refuses a second order for the same (tenant, proposal). Generating
    it on this side is what makes a retried submission safe: the retry carries
    the same id, so it collapses into the original rather than placing a second
    order.

    `rationale` travels with the proposal because the worker stores it on the
    order row. A conversation can be compacted away; an order that outlives the
    reasoning behind it is one nobody can review later.
    """

    tenant_id: TenantId
    account_id: str
    symbol: str
    side: Side
    quantity: int
    limit_price: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    timeout_millis: int = 0
    rationale: str = ""
    proposal_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not self.symbol.strip():
            raise ValueError("a proposal must name a symbol")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit price must be positive when given")

    @property
    def is_market_order(self) -> bool:
        """No limit means market. Worth naming, because the two behave
        differently enough that reading `limit_price is None` at a call site
        invites forgetting which way round it is."""
        return self.limit_price is None

    def to_wire(self) -> dict[str, object]:
        """The worker's `TradeProposal` shape.

        Field names are its, not ours. Kept in one place so that a rename on
        that side is a change to this method rather than a hunt.
        """
        return {
            "proposalId": str(self.proposal_id),
            "tenantId": str(self.tenant_id),
            "accountId": self.account_id,
            "symbol": self.symbol,
            "action": str(self.side),
            "quantity": self.quantity,
            "limitPrice": _decimal(self.limit_price),
            "takeProfit": _decimal(self.take_profit),
            "stopLoss": _decimal(self.stop_loss),
            "timeoutMillis": self.timeout_millis,
            "rationale": self.rationale,
        }


def _decimal(value: Decimal | None) -> str | None:
    """Money crosses the wire as text.

    A float would round it, and the rounding would happen silently at the point
    where a price becomes a number rather than at the point where anyone could
    notice.
    """
    return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class OrderView:
    """An order, as the worker last recorded it.

    Read-only by construction. The worker is the only writer: two writers to an
    order row is how a fill gets overwritten by a status that was already
    stale when it was read.
    """

    broker_order_id: str
    proposal_id: str
    symbol: str
    side: Side
    quantity: int
    status: OrderStatus
    filled_quantity: int = 0
    average_price: Decimal = Decimal(0)
    limit_price: Decimal | None = None
    rationale: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def remaining(self) -> int:
        """Unfilled quantity, floored at zero.

        Floored because an overfill is possible — a broker can report more than
        was asked for — and a negative remainder rendered in an interface reads
        as a bug rather than as the unusual event it is.
        """
        return max(0, self.quantity - self.filled_quantity)

    @property
    def notional(self) -> Decimal:
        """What has actually changed hands so far."""
        return self.average_price * self.filled_quantity


@dataclass(frozen=True, slots=True)
class Position:
    """A holding, derived from fills.

    Derived rather than stored: the orders are the record, and a separately
    maintained position table is a second copy that can disagree with them.
    """

    symbol: str
    quantity: int
    average_cost: Decimal

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def cost_basis(self) -> Decimal:
        return self.average_cost * abs(self.quantity)


class OrderGateway(Protocol):
    """How a proposal leaves this process.

    A port rather than a client, because what is on the other side differs by
    deployment: a durable table drained by a relay, a message broker, or in a
    test, a list. What every implementation owes is that a proposal accepted
    here will eventually reach the worker exactly once — which is why the
    idempotency key is generated before this call rather than inside it.
    """

    async def submit(self, proposal: TradeProposal) -> None: ...

    async def cancel(self, tenant_id: TenantId, broker_order_id: str) -> None: ...
