"""Placing orders and reading positions.

The route does three things and delegates the rest: it checks the risk
envelope, queues the proposal, and reports what the execution worker has since
recorded. Everything about actually reaching a broker happens in the worker,
which is why nothing here can hang on one.

A submission returns 202 rather than 201. The order does not exist yet — a
proposal has been accepted for delivery, and the broker id that would identify
an order is not assigned until the worker has been to the broker. Returning 201
with a location nobody can fetch yet would be a lie about what happened.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from kairos.core.tenancy.context import current_scope
from kairos.core.trading import (
    AccountState,
    OrderStatus,
    OrderView,
    Position,
    RiskLimits,
    Side,
    TradeProposal,
    assess,
)

router = APIRouter(tags=["trading"])


def get_orders():  # type: ignore[no-untyped-def]
    """Read access to the execution worker's orders."""
    raise NotImplementedError("order reader is not wired")


def get_gateway():  # type: ignore[no-untyped-def]
    """Where a proposal goes on its way out."""
    raise NotImplementedError("order gateway is not wired")


def get_limits() -> RiskLimits:
    """The tenant's risk envelope.

    Overridden by the composition root. The default is the conservative one,
    so a deployment that forgets to configure limits gets tight ones rather
    than none.
    """
    return RiskLimits()


def get_account():  # type: ignore[no-untyped-def]
    """Equity and today's order count, for sizing."""
    raise NotImplementedError("account state is not wired")


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class OrderRequest(BaseModel):
    """What a caller asks for.

    `reference_price` is required for a market order and refused for a limit
    order, because in the second case the limit already is the reference and
    accepting a second number invites the two disagreeing.
    """

    symbol: Annotated[str, Field(min_length=1, max_length=32)]
    side: Side
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]
    limit_price: Decimal | None = None
    reference_price: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    rationale: Annotated[str, Field(max_length=4000)] = ""
    account_id: Annotated[str, Field(min_length=1, max_length=64)] = "paper"


class OrderAccepted(BaseModel):
    proposal_id: str
    status: str = "queued"
    detail: list[str] = Field(default_factory=list)


class OrderRefused(BaseModel):
    refusals: list[str]
    detail: list[str]


class OrderResponse(BaseModel):
    broker_order_id: str
    proposal_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    remaining: int
    status: str
    terminal: bool
    average_price: str
    limit_price: str | None
    rationale: str
    created_at: str | None
    updated_at: str | None


class OrderList(BaseModel):
    orders: list[OrderResponse]


class PositionResponse(BaseModel):
    symbol: str
    quantity: int
    average_cost: str
    cost_basis: str


class PositionList(BaseModel):
    positions: list[PositionResponse]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/orders",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OrderAccepted,
    responses={422: {"model": OrderRefused}},
)
async def place_order(
    request: OrderRequest,
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
    gateway=Depends(get_gateway),  # type: ignore[no-untyped-def]
    limits: RiskLimits = Depends(get_limits),
    account=Depends(get_account),  # type: ignore[no-untyped-def]
) -> OrderAccepted:
    """Propose a trade.

    The envelope is checked here, while a person is still looking at the form.
    The worker checks it again and its refusal is the one that counts — but its
    refusal arrives seconds later as a `DENIED` status attached to an order
    nobody wanted, and it cannot say which limit was hit and by how much.
    """
    if request.limit_price is not None and request.reference_price is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "a limit order already has a reference price; supplying both "
            "invites the two disagreeing",
        )

    scope = current_scope()
    proposal = TradeProposal(
        tenant_id=scope.tenant_id,
        account_id=request.account_id,
        symbol=request.symbol.upper(),
        side=request.side,
        quantity=request.quantity,
        limit_price=request.limit_price,
        take_profit=request.take_profit,
        stop_loss=request.stop_loss,
        rationale=request.rationale,
    )

    if proposal.is_market_order and request.reference_price is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "a market order needs a reference price; without one it cannot be "
            "measured against any limit",
        )

    state = await _account_state(account, orders)
    decision = assess(
        proposal, state, limits, reference_price=request.reference_price
    )
    if decision.refused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "refusals": [str(r) for r in decision.refusals],
                "detail": list(decision.detail),
            },
        )

    # Queued, not sent. The relay delivers; this call returning does not mean
    # a broker has seen anything.
    await gateway.submit(proposal)

    return OrderAccepted(proposal_id=str(proposal.proposal_id), detail=list(decision.detail))


@router.get("/orders", response_model=OrderList)
async def list_orders(
    working_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
) -> OrderList:
    """This tenant's orders.

    No ownership check here either: the reader filters on the ambient scope,
    so a handler that forgot one returns nothing rather than everything.
    """
    found = await (orders.working() if working_only else orders.recent(limit=limit))
    return OrderList(orders=[_render(o) for o in found])


@router.get("/orders/{broker_order_id}", response_model=OrderResponse)
async def get_order(
    broker_order_id: str,
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
) -> OrderResponse:
    order = await orders.by_broker_id(broker_order_id)
    if order is None:
        # The same answer whether it does not exist or belongs to someone else.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return _render(order)


@router.get("/proposals/{proposal_id}", response_model=OrderResponse)
async def get_by_proposal(
    proposal_id: str,
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
) -> OrderResponse:
    """What became of a proposal.

    The only handle a caller has immediately after submitting: the broker id
    does not exist until the worker has been to the broker, so polling by
    proposal is how a client watches its own order appear.
    """
    order = await orders.by_proposal(proposal_id)
    if order is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No order for that proposal yet",
        )
    return _render(order)


@router.delete("/orders/{broker_order_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_order(
    broker_order_id: str,
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
    gateway=Depends(get_gateway),  # type: ignore[no-untyped-def]
) -> dict[str, str]:
    """Ask for an order to be cancelled.

    202, not 204. Cancellation is a request to the broker that may lose a race
    with a fill — reporting it as done would tell a user their order is
    cancelled when it may have just filled.
    """
    order = await orders.by_broker_id(broker_order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    if order.status.terminal:
        # Nothing to cancel, and queueing a message for the worker to discover
        # that would be noise. Reported as a conflict rather than success so a
        # client does not show a spinner waiting for a state change that will
        # never come.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Order is already {order.status}",
        )

    await gateway.cancel(current_scope().tenant_id, broker_order_id)
    return {"status": "cancel_requested", "broker_order_id": broker_order_id}


@router.get("/positions", response_model=PositionList)
async def list_positions(
    orders=Depends(get_orders),  # type: ignore[no-untyped-def]
) -> PositionList:
    """Holdings, derived from fills."""
    positions = await orders.positions()
    return PositionList(positions=[_position(p) for p in positions])


# ---------------------------------------------------------------------------


async def _account_state(account, orders) -> AccountState:  # type: ignore[no-untyped-def]
    """What the envelope measures against.

    Positions come from the orders rather than from the account service,
    because the orders are the record. Asking two sources and hoping they agree
    is how a limit ends up enforced against a position that does not exist.
    """
    equity = await account.equity()
    held = {p.symbol: p for p in await orders.positions()}
    return AccountState(
        equity=equity,
        positions=held,
        orders_today=await account.orders_today(),
    )


def _render(order: OrderView) -> OrderResponse:
    return OrderResponse(
        broker_order_id=order.broker_order_id,
        proposal_id=order.proposal_id,
        symbol=order.symbol,
        side=str(order.side),
        quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        remaining=order.remaining,
        status=str(order.status),
        # Sent rather than left for the client to derive. A client that
        # computes it from a status list has its own copy of the state machine,
        # and the two drift the first time a status is added.
        terminal=order.status.terminal,
        average_price=str(order.average_price),
        limit_price=None if order.limit_price is None else str(order.limit_price),
        rationale=order.rationale,
        created_at=order.created_at.isoformat() if order.created_at else None,
        updated_at=order.updated_at.isoformat() if order.updated_at else None,
    )


def _position(position: Position) -> PositionResponse:
    return PositionResponse(
        symbol=position.symbol,
        quantity=position.quantity,
        average_cost=str(position.average_cost),
        cost_basis=str(position.cost_basis),
    )


__all__ = ["OrderStatus", "router"]
