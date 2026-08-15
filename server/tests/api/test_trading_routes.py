"""Placing orders over HTTP.

The route's job is to refuse early and honestly. So these check the refusals
and the status codes that describe what actually happened — a proposal
accepted for delivery is not an order, and a cancel that was asked for is not
a cancel that has taken effect.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from kairos.core.trading import OrderStatus, OrderView, Position, Side, TradeProposal
from kairos.runtime.app import create_app, dependency_overrides_for
from kairos.runtime.settings import (
    AuthSettings,
    Deployment,
    Settings,
    TradingSettings,
)


def order(**overrides: object) -> OrderView:
    base: dict[str, object] = {
        "broker_order_id": "ib-1",
        "proposal_id": "p-1",
        "symbol": "NVDA",
        "side": Side.BUY,
        "quantity": 10,
        "status": OrderStatus.ACCEPTED,
        "filled_quantity": 0,
        "average_price": Decimal("0"),
    }
    return OrderView(**(base | overrides))  # type: ignore[arg-type]


class FakeOrders:
    def __init__(self, orders: list[OrderView] | None = None, positions: list[Position] | None = None) -> None:
        self.orders = orders or []
        self._positions = positions or []

    async def recent(self, *, limit: int = 50) -> list[OrderView]:
        return self.orders[:limit]

    async def working(self) -> list[OrderView]:
        return [o for o in self.orders if o.status.working]

    async def by_broker_id(self, broker_order_id: str) -> OrderView | None:
        return next((o for o in self.orders if o.broker_order_id == broker_order_id), None)

    async def by_proposal(self, proposal_id: str) -> OrderView | None:
        return next((o for o in self.orders if o.proposal_id == proposal_id), None)

    async def positions(self) -> list[Position]:
        return self._positions


class FakeGateway:
    def __init__(self) -> None:
        self.submitted: list[TradeProposal] = []
        self.cancelled: list[str] = []

    async def submit(self, proposal: TradeProposal) -> None:
        self.submitted.append(proposal)

    async def cancel(self, tenant_id: object, broker_order_id: str) -> None:
        self.cancelled.append(broker_order_id)


class FakeAccount:
    def __init__(self, equity: Decimal = Decimal("100000"), today: int = 0) -> None:
        self._equity = equity
        self._today = today

    async def equity(self) -> Decimal:
        return self._equity

    async def orders_today(self) -> int:
        return self._today


def build(
    *,
    orders: FakeOrders | None = None,
    account: FakeAccount | None = None,
    trading: TradingSettings | None = None,
) -> tuple[TestClient, FakeGateway, FakeOrders]:
    app = create_app(
        settings=Settings(
            deployment=Deployment.SOLO,
            auth=AuthSettings(),
            trading=trading or TradingSettings(),
        )
    )
    reader = orders or FakeOrders()
    gateway = FakeGateway()
    dependency_overrides_for(
        app, orders=reader, gateway=gateway, account=account or FakeAccount()
    )
    return TestClient(app), gateway, reader


def body(**overrides: object) -> dict:
    base: dict[str, object] = {
        "symbol": "nvda",
        "side": "BUY",
        "quantity": 10,
        "limit_price": "100",
        "rationale": "earnings beat",
    }
    return base | overrides


class TestPlacingAnOrder:
    def test_an_accepted_proposal_returns_202(self) -> None:
        """202, not 201. The order does not exist yet — a proposal has been
        accepted for delivery, and the broker id that would identify an order
        is not assigned until the worker has been to the broker."""
        client, gateway, _ = build()
        response = client.post("/api/v1/orders", json=body())

        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert len(gateway.submitted) == 1

    def test_the_symbol_is_normalised(self) -> None:
        client, gateway, _ = build()
        client.post("/api/v1/orders", json=body(symbol="nvda"))
        assert gateway.submitted[0].symbol == "NVDA"

    def test_the_rationale_travels_with_the_proposal(self) -> None:
        """A conversation can be compacted away. An order that outlives the
        reasoning behind it is one nobody can review later."""
        client, gateway, _ = build()
        client.post("/api/v1/orders", json=body(rationale="beat on datacenter"))
        assert gateway.submitted[0].rationale == "beat on datacenter"

    def test_the_client_gets_a_handle_it_can_poll(self) -> None:
        client, gateway, _ = build()
        returned = client.post("/api/v1/orders", json=body()).json()["proposal_id"]
        assert returned == str(gateway.submitted[0].proposal_id)


class TestRefusals:
    def test_an_oversized_order_is_refused_before_it_is_queued(self) -> None:
        """The worker would refuse it too, but seconds later and as a DENIED
        status attached to an order nobody wanted."""
        client, gateway, _ = build()
        response = client.post("/api/v1/orders", json=body(quantity=100))

        assert response.status_code == 422
        assert gateway.submitted == []

    def test_the_refusal_names_the_limit_that_was_hit(self) -> None:
        client, _, _ = build()
        detail = client.post("/api/v1/orders", json=body(quantity=100)).json()["detail"]
        assert "order_too_large" in detail["refusals"]
        assert any("of equity" in d for d in detail["detail"])

    def test_a_market_order_without_a_reference_price_is_refused(self) -> None:
        """Treating an unpriced order as free would let it past every check."""
        client, _, _ = build()
        response = client.post("/api/v1/orders", json=body(limit_price=None))
        assert response.status_code == 422

    def test_a_market_order_with_a_reference_price_is_measured(self) -> None:
        client, gateway, _ = build()
        response = client.post(
            "/api/v1/orders", json=body(limit_price=None, reference_price="100")
        )
        assert response.status_code == 202
        assert gateway.submitted[0].is_market_order

    def test_supplying_both_prices_is_refused(self) -> None:
        """A limit order already has a reference; a second number invites the
        two disagreeing."""
        client, _, _ = build()
        response = client.post(
            "/api/v1/orders", json=body(limit_price="100", reference_price="105")
        )
        assert response.status_code == 422

    def test_the_daily_cap_is_enforced(self) -> None:
        client, gateway, _ = build(account=FakeAccount(today=3))
        assert client.post("/api/v1/orders", json=body()).status_code == 422
        assert gateway.submitted == []

    def test_a_symbol_outside_the_universe_is_refused(self) -> None:
        client, _, _ = build(trading=TradingSettings(universe=("aapl",)))
        detail = client.post("/api/v1/orders", json=body(symbol="NVDA")).json()["detail"]
        assert "not_in_universe" in detail["refusals"]

    def test_the_universe_is_matched_case_insensitively(self) -> None:
        """Configured lowercase, submitted uppercase — the same symbol."""
        client, _, _ = build(trading=TradingSettings(universe=("nvda",)))
        assert client.post("/api/v1/orders", json=body(symbol="nvda")).status_code == 202


class TestReadingOrders:
    def test_orders_are_listed(self) -> None:
        client, _, _ = build(orders=FakeOrders([order()]))
        listed = client.get("/api/v1/orders").json()["orders"]
        assert listed[0]["broker_order_id"] == "ib-1"

    def test_terminality_is_sent_rather_than_derived(self) -> None:
        """A client that computes it from a status list has its own copy of the
        state machine, and the two drift the first time a status is added."""
        client, _, _ = build(orders=FakeOrders([order(status=OrderStatus.FILLED)]))
        assert client.get("/api/v1/orders").json()["orders"][0]["terminal"] is True

    def test_working_only_filters(self) -> None:
        client, _, _ = build(
            orders=FakeOrders([order(status=OrderStatus.FILLED), order(broker_order_id="ib-2")])
        )
        listed = client.get("/api/v1/orders?working_only=true").json()["orders"]
        assert [o["broker_order_id"] for o in listed] == ["ib-2"]

    def test_an_unknown_order_is_404(self) -> None:
        client, _, _ = build()
        assert client.get("/api/v1/orders/nope").status_code == 404

    def test_an_order_is_findable_by_proposal(self) -> None:
        """The only handle a caller has immediately after submitting."""
        client, _, _ = build(orders=FakeOrders([order(proposal_id="p-42")]))
        assert client.get("/api/v1/proposals/p-42").status_code == 200

    def test_a_proposal_with_no_order_yet_is_404(self) -> None:
        client, _, _ = build()
        assert client.get("/api/v1/proposals/p-42").status_code == 404


class TestCancelling:
    def test_a_cancel_is_accepted_not_completed(self) -> None:
        """202, not 204. Cancellation can lose a race with a fill; reporting it
        as done would tell a user their order is cancelled when it may have
        just filled."""
        client, gateway, _ = build(orders=FakeOrders([order()]))
        response = client.delete("/api/v1/orders/ib-1")

        assert response.status_code == 202
        assert gateway.cancelled == ["ib-1"]

    def test_cancelling_a_finished_order_is_a_conflict(self) -> None:
        """Otherwise a client shows a spinner waiting for a state change that
        will never come."""
        client, gateway, _ = build(orders=FakeOrders([order(status=OrderStatus.FILLED)]))
        response = client.delete("/api/v1/orders/ib-1")

        assert response.status_code == 409
        assert gateway.cancelled == []

    def test_cancelling_an_unknown_order_is_404(self) -> None:
        client, _, _ = build()
        assert client.delete("/api/v1/orders/nope").status_code == 404


class TestPositions:
    def test_positions_are_listed(self) -> None:
        client, _, _ = build(
            orders=FakeOrders(positions=[Position("NVDA", 10, Decimal("100"))])
        )
        listed = client.get("/api/v1/positions").json()["positions"]
        assert listed[0] == {
            "symbol": "NVDA",
            "quantity": 10,
            "average_cost": "100",
            "cost_basis": "1000",
        }

    def test_holdings_are_measured_against_the_position_limit(self) -> None:
        """Sizing reads positions from the orders rather than from an account
        service, so a limit cannot be enforced against a position that does not
        exist."""
        client, gateway, _ = build(
            orders=FakeOrders(positions=[Position("NVDA", 295, Decimal("100"))])
        )
        response = client.post("/api/v1/orders", json=body(quantity=10))

        assert response.status_code == 422
        assert "position_too_large" in response.json()["detail"]["refusals"]
