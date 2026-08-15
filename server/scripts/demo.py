"""Run the service against a scripted model, with no external dependencies.

Proves the assembled system works end to end without needing a database, a
provider key, or a network. Everything real is real except the model itself,
which replays a fixed script — because the point is to exercise the wiring, and
a live model would make the demo depend on the weather.

    python scripts/demo.py            # start the server on :8000
    python scripts/demo.py --probe    # start it, drive it, print the stream
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kairos.adapters.services.analyst import AnalystRating, Grade  # noqa: E402
from kairos.adapters.services.billing import (  # noqa: E402
    Membership,
    OrderState,
    Plan,
    RechargeOrder,
)
from kairos.core.catalog import (  # noqa: E402
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
from kairos.core.reasoning.engine import ModelReply, ReasoningEngine  # noqa: E402
from kairos.core.reasoning.turn import ToolOutcome, ToolRequest  # noqa: E402
from kairos.core.tools import Exposure, ToolDefinition, ToolRegistry  # noqa: E402
from kairos.core.trading import OrderStatus, OrderView, Position, Side  # noqa: E402
from kairos.runtime.app import create_app, dependency_overrides_for  # noqa: E402
from kairos.runtime.settings import AuthSettings, Deployment, Settings  # noqa: E402

THREAD_ID = UUID("11111111-1111-1111-1111-111111111111")

CATALOG = Catalog(
    providers=[
        ProviderDescriptor(
            id=ProviderId("demo"),
            display_name="Demo",
            endpoint=Endpoint(wire=Wire.OPENAI_CHAT, credential_env="DEMO_KEY"),
        )
    ],
    models=[
        ModelDescriptor(
            id=ModelId("demo-large"),
            remote_id="demo-large",
            provider=ProviderId("demo"),
            budget=TokenBudget(context=200_000, max_output=8_000),
            capabilities=Capability.baseline() | Capability.VISION,
        ),
        ModelDescriptor(
            id=ModelId("demo-small"),
            remote_id="demo-small",
            provider=ProviderId("demo"),
            budget=TokenBudget(context=32_000, max_output=2_000),
            capabilities=Capability.baseline(),
        ),
    ],
)


class Thread:
    """The one thread this demo serves."""

    def __init__(self) -> None:
        self.id = THREAD_ID
        self.title = "Demo thread"
        self.workspace_id = uuid4()
        self.updated_at = datetime.now(UTC)


class Threads:
    def __init__(self) -> None:
        self._thread = Thread()

    async def get(self, thread_id: UUID) -> Thread | None:
        return self._thread if thread_id == THREAD_ID else None

    async def list_own(self, *, limit: int = 50, offset: int = 0) -> list[Thread]:
        return [self._thread]

    async def remove(self, thread_id: UUID) -> None:
        pass


class Tools:
    """A tool that answers instantly, so the demo needs nothing outside itself."""

    async def run(self, request: ToolRequest) -> ToolOutcome:
        await asyncio.sleep(0.15)  # visible in the stream, so ordering is observable
        return ToolOutcome(
            call_id=request.call_id,
            ok=True,
            summary=f"{request.name} returned 3 results",
        )


class Transcripts:
    async def for_thread(self, thread_id: UUID):  # type: ignore[no-untyped-def]
        return None


class Preferences:
    """Model choices, held in memory for the life of the process.

    Persisted only this far, but persisted honestly: a change made through the
    settings page is visible on the next read, which is the behaviour the
    resolution chain is supposed to have.
    """

    def __init__(self) -> None:
        self._by_role: dict[str, str] = {}

    async def as_mapping(self) -> dict[str, str]:
        return dict(self._by_role)

    async def set_role(self, role: str, model_id: str) -> None:
        self._by_role[role] = model_id

    async def clear_role(self, role: str) -> None:
        self._by_role.pop(role, None)


class Turns:
    async def token_usage(self) -> tuple[int, int]:
        return 930, 123  # what the scripted conversation below actually spends


class Repositories:
    def __init__(self) -> None:
        self.threads = Threads()
        self.tools = Tools()
        self.transcripts = Transcripts()
        self.preferences = Preferences()
        self.turns = Turns()


class ScriptedModel:
    """Two rounds: ask for a tool, then answer using its result."""

    def __init__(self) -> None:
        self._round = 0

    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.2)
        self._round += 1
        if self._round == 1:
            return ModelReply(
                reasoning="The question needs current data; searching first.",
                tool_requests=(
                    ToolRequest(
                        call_id="call-1",
                        name="search",
                        arguments={"query": "quarterly revenue"},
                    ),
                ),
                input_tokens=420,
                output_tokens=35,
                model_id="demo-large",
            )
        return ModelReply(
            text="Revenue grew 12% quarter over quarter, driven by services.",
            input_tokens=510,
            output_tokens=88,
            model_id="demo-large",
        )


class Orders:
    """The execution worker's order book, in memory.

    Stands in for the `execution` schema the worker writes. Orders here move
    from accepted to filled after a short delay, so the interface's polling and
    its partial-fill rendering are exercised rather than assumed.
    """

    def __init__(self) -> None:
        self._orders: list[OrderView] = []
        self._placed_at: dict[str, float] = {}
        self._sequence = 0

    def accept(self, proposal) -> None:  # type: ignore[no-untyped-def]
        self._sequence += 1
        broker_id = f"ib-{self._sequence}"
        self._orders.append(
            OrderView(
                broker_order_id=broker_id,
                proposal_id=str(proposal.proposal_id),
                symbol=proposal.symbol,
                side=proposal.side,
                quantity=proposal.quantity,
                status=OrderStatus.ACCEPTED,
                limit_price=proposal.limit_price,
                rationale=proposal.rationale,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        self._placed_at[broker_id] = time.monotonic()

    def _aged(self) -> list[OrderView]:
        """Advance orders by how long they have been sitting.

        Time-based rather than call-based: a client that polls twice as often
        should not see fills twice as fast, which is exactly the artefact a
        counter would produce.
        """
        now = time.monotonic()
        advanced: list[OrderView] = []
        for order in self._orders:
            age = now - self._placed_at.get(order.broker_order_id, now)
            if order.status.terminal or age < 3:
                advanced.append(order)
            elif age < 6:
                half = max(1, order.quantity // 2)
                advanced.append(
                    replace(
                        order,
                        status=OrderStatus.PARTIALLY_FILLED,
                        filled_quantity=half,
                        average_price=order.limit_price or Decimal("100"),
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                advanced.append(
                    replace(
                        order,
                        status=OrderStatus.FILLED,
                        filled_quantity=order.quantity,
                        average_price=order.limit_price or Decimal("100"),
                        updated_at=datetime.now(UTC),
                    )
                )
        self._orders = advanced
        return advanced

    async def recent(self, *, limit: int = 50) -> list[OrderView]:
        return list(reversed(self._aged()))[:limit]

    async def working(self) -> list[OrderView]:
        return [o for o in self._aged() if o.status.working]

    async def by_broker_id(self, broker_order_id: str) -> OrderView | None:
        return next(
            (o for o in self._aged() if o.broker_order_id == broker_order_id), None
        )

    async def by_proposal(self, proposal_id: str) -> OrderView | None:
        return next((o for o in self._aged() if o.proposal_id == proposal_id), None)

    async def positions(self) -> list[Position]:
        held: dict[str, tuple[int, Decimal]] = {}
        for order in self._aged():
            if order.filled_quantity == 0:
                continue
            quantity, cost = held.get(order.symbol, (0, Decimal(0)))
            if order.side is Side.BUY:
                spent = cost * quantity + order.notional
                quantity += order.filled_quantity
                cost = spent / quantity if quantity else Decimal(0)
            else:
                quantity -= order.filled_quantity
            held[order.symbol] = (quantity, cost)

        return [
            Position(symbol=symbol, quantity=quantity, average_cost=cost)
            for symbol, (quantity, cost) in sorted(held.items())
            if quantity
        ]


class Gateway:
    """Delivers straight to the order book.

    The real gateway writes to an outbox that a relay drains to a broker. Here
    the two steps collapse, because the demo's point is the path through the
    platform rather than the durability of the queue — which has its own tests.
    """

    def __init__(self, orders: Orders) -> None:
        self._orders = orders

    async def submit(self, proposal) -> None:  # type: ignore[no-untyped-def]
        self._orders.accept(proposal)

    async def cancel(self, tenant_id, broker_order_id: str) -> None:  # type: ignore[no-untyped-def]
        pass


class Account:
    """Enough equity to place a few orders and hit the limits with a big one."""

    async def equity(self) -> Decimal:
        return Decimal("100000")

    async def orders_today(self) -> int:
        return 0


class Analyst:
    """A rating for one symbol, and nothing for the rest.

    Deliberately partial: the interface's "no coverage" path is the one most
    likely to be wrong, and a demo where every symbol has data never shows it.
    """

    RATED = {
        "NVDA": AnalystRating(
            symbol="NVDA",
            company_name="NVIDIA",
            consensus="Strong Buy",
            target_price=210.0,
            target_high=250.0,
            target_low=160.0,
            analyst_count=42,
            recent_grades=(
                Grade(firm="Demo Capital", from_grade="Hold", to_grade="Buy", action="upgrade"),
                Grade(firm="Example Bank", from_grade="Buy", to_grade="Buy", action="maintain"),
            ),
            updated_at=datetime.now(UTC),
        )
    }

    async def rating(self, symbol: str) -> AnalystRating | None:
        return self.RATED.get(symbol.upper())

    async def cache_stats(self) -> dict[str, int]:
        return {"l1_hits": 831, "l2_hits": 164, "misses": 5}


class Billing:
    """Plans, and a top-up that settles a few seconds after it is started."""

    PLANS = [
        Plan(id=1, name="Monthly", price=Decimal("29.00"), duration_days=30),
        Plan(id=2, name="Yearly", price=Decimal("290.00"), duration_days=365),
    ]

    def __init__(self) -> None:
        self._orders: dict[int, tuple[RechargeOrder, float]] = {}
        # Request id to order id. Per instance, not on the class: a class-level
        # mutable would be shared by every Billing ever constructed, which in a
        # test suite means one case seeing another's orders.
        self._by_request: dict[str, int] = {}
        self._sequence = 0

    async def plans(self) -> list[Plan]:
        return list(self.PLANS)

    async def membership(self, user_id: str) -> Membership:
        return Membership(level=0)

    async def start_recharge(
        self, user_id: str, plan_id: int, *, request_id: str
    ) -> RechargeOrder:
        # Keyed on the request id, so a double-clicked buy returns the same
        # order rather than creating a second one — the property the real
        # service's idempotency guard provides.
        existing = self._by_request.get(request_id)
        if existing is not None:
            return self._orders[existing][0]

        self._sequence += 1
        plan = next((p for p in self.PLANS if p.id == plan_id), self.PLANS[0])
        order = RechargeOrder(
            id=self._sequence,
            plan_id=plan_id,
            amount=plan.price,
            state=OrderState.UNPAID,
            created_at=datetime.now(UTC),
        )
        self._orders[order.id] = (order, time.monotonic())
        self._by_request[request_id] = order.id
        return order

    async def order(self, user_id: str, order_id: int) -> RechargeOrder | None:
        found = self._orders.get(order_id)
        if found is None:
            return None

        order, started = found
        if order.state is OrderState.UNPAID and time.monotonic() - started > 4:
            order = replace(order, state=OrderState.PAID, paid_at=datetime.now(UTC))
            self._orders[order_id] = (order, started)
        return order


def build_app():  # type: ignore[no-untyped-def]
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search",
            description="Search filings and news",
            exposure=Exposure.DIRECT,
        )
    )

    app = create_app(
        settings=Settings(deployment=Deployment.SOLO, auth=AuthSettings()),
        catalog=CATALOG,
    )
    orders = Orders()
    dependency_overrides_for(
        app,
        engine=ReasoningEngine(ScriptedModel(), registry),
        repositories=Repositories(),
        orders=orders,
        gateway=Gateway(orders),
        account=Account(),
        analyst=Analyst(),
        billing=Billing(),
    )
    return app


def probe() -> int:
    """Drive the app in-process and print what a client would receive."""
    from fastapi.testclient import TestClient

    client = TestClient(build_app())

    print("── health ─────────────────────────────────────────────")
    print(client.get("/health").json())

    print("\n── threads ────────────────────────────────────────────")
    print(client.get("/api/v1/threads").json())

    print("\n── streamed answer ────────────────────────────────────")
    with client.stream(
        "POST",
        f"/api/v1/threads/{THREAD_ID}/messages",
        json={"prompt": "How did revenue do last quarter?"},
    ) as response:
        print(f"status      {response.status_code}")
        print(f"content-type {response.headers['content-type']}")
        print(f"reconnect   {response.headers['content-location']}\n")
        for line in response.iter_lines():
            if line:
                print(f"  {line}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="drive it and exit")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()

    if arguments.probe:
        return probe()

    import uvicorn

    print(f"http://127.0.0.1:{arguments.port}/docs")
    print(f"thread id: {THREAD_ID}")
    uvicorn.run(build_app(), host="127.0.0.1", port=arguments.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
