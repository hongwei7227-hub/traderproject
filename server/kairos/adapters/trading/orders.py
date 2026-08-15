"""Reading the orders the execution worker wrote.

This table belongs to another service. It is described here with a Core
`Table` rather than mapped as one of this package's entities, and that is a
deliberate signal: an ORM entity implies ownership — a place to attach
relationships, cascades and writes — and none of those are ours to have. The
worker's migrations create it; ours must not.

There is exactly one writer, and it is not this process. A second writer is how
a fill gets overwritten by a status that was already stale when it was read.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from kairos.core.tenancy.context import current_scope
from kairos.core.trading.orders import OrderStatus, OrderView, Position, Side

# Separate metadata from the platform's own. Sharing it would put this table
# into `create_all`, and this process creating another service's table is how
# two migration histories start disagreeing about a column.
foreign_metadata = MetaData()

# One `Table` per schema. Defining the same table twice against one metadata
# raises, and this is called per request — so the cache is not an optimisation,
# it is what makes the function callable more than once.
_tables: dict[str | None, Table] = {}


def order_table(schema: str | None = "execution") -> Table:
    """The worker's `orders` table.

    `schema` is a parameter rather than a constant because the suite runs
    against SQLite, which has no schemas. Passing `None` there keeps the
    queries under test identical to the ones that run in production apart from
    the qualifier — which is the part least likely to be where a bug is.

    Columns mirror the worker's migration exactly, including the ones this
    platform does not read. Listing only the interesting ones would make a
    `SELECT *` fail the day someone wrote one.
    """
    if schema in _tables:
        return _tables[schema]

    table = Table(
        "orders",
        foreign_metadata,
        Column("broker_order_id", String(64), primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("proposal_id", String(64), nullable=False),
        Column("symbol", String(32), nullable=False),
        Column("action", String(8), nullable=False),
        Column("quantity", BigInteger, nullable=False),
        Column("limit_price", Numeric(18, 4)),
        Column("status", String(24), nullable=False),
        Column("filled_qty", BigInteger, nullable=False, default=0),
        Column("avg_px", Numeric(18, 4), nullable=False, default=0),
        Column("created_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True)),
        Column("rationale", Text),
        schema=schema,
    )
    _tables[schema] = table
    return table


class ExecutionOrders:
    """Read-only access to a tenant's orders.

    Every query filters on the ambient tenant, the same as the platform's own
    repositories. The table being foreign does not make the isolation someone
    else's problem — this process is the one exposing it over HTTP.
    """

    __slots__ = ("_session", "_orders")

    def __init__(self, session: AsyncSession, *, schema: str | None = "execution") -> None:
        self._session = session
        self._orders = order_table(schema)

    def _scoped(self):  # type: ignore[no-untyped-def]
        return select(self._orders).where(
            self._orders.c.tenant_id == str(current_scope().tenant_id)
        )

    async def recent(self, *, limit: int = 50) -> list[OrderView]:
        result = await self._session.execute(
            self._scoped().order_by(self._orders.c.created_at.desc()).limit(limit)
        )
        return [_view(row) for row in result.mappings()]

    async def working(self) -> list[OrderView]:
        """Orders that can still fill.

        Filtered in the database rather than in Python: a tenant with a long
        history should not have every order it ever placed cross the wire so
        that three can be shown.
        """
        live = [str(s) for s in OrderStatus if s.working]
        result = await self._session.execute(
            self._scoped()
            .where(self._orders.c.status.in_(live))
            .order_by(self._orders.c.created_at.desc())
        )
        return [_view(row) for row in result.mappings()]

    async def by_broker_id(self, broker_order_id: str) -> OrderView | None:
        result = await self._session.execute(
            self._scoped().where(self._orders.c.broker_order_id == broker_order_id)
        )
        row = result.mappings().first()
        return _view(row) if row else None

    async def by_proposal(self, proposal_id: str) -> OrderView | None:
        """What became of a proposal this platform submitted.

        The join back from "what I asked for" to "what happened" — the outbox
        knows the proposal id and nothing else, because the broker id does not
        exist until the worker has been to the broker.
        """
        result = await self._session.execute(
            self._scoped().where(self._orders.c.proposal_id == proposal_id)
        )
        row = result.mappings().first()
        return _view(row) if row else None

    async def positions(self) -> list[Position]:
        """Holdings, derived from filled orders.

        Derived rather than read from a positions table, because the orders are
        the record. A separately maintained table is a second copy that can
        disagree with them, and reconciling the two is a job nobody wants.

        Computed in Python rather than as a grouped query: the arithmetic —
        weighted average cost surviving a partial sell — is the part worth
        being able to read and test, and expressing it in SQL would hide it.
        """
        orders = await self._filled()

        quantities: dict[str, int] = {}
        costs: dict[str, Decimal] = {}
        for order in orders:
            signed = order.filled_quantity if order.side is Side.BUY else -order.filled_quantity
            held = quantities.get(order.symbol, 0)

            if order.side is Side.BUY:
                # Weighted average of what is held and what was just bought.
                spent = costs.get(order.symbol, Decimal(0)) * held + order.notional
                quantities[order.symbol] = held + signed
                if quantities[order.symbol]:
                    costs[order.symbol] = spent / quantities[order.symbol]
            else:
                # A sale realises profit or loss; it does not change the cost
                # of what remains. Recomputing the average on a sell is a
                # common way to make a position's basis drift.
                quantities[order.symbol] = held + signed

        return [
            Position(symbol=symbol, quantity=quantity, average_cost=costs.get(symbol, Decimal(0)))
            for symbol, quantity in sorted(quantities.items())
            if quantity != 0
        ]

    async def _filled(self) -> Sequence[OrderView]:
        result = await self._session.execute(
            self._scoped()
            .where(self._orders.c.filled_qty > 0)
            .order_by(self._orders.c.created_at)
        )
        return [_view(row) for row in result.mappings()]


def _view(row) -> OrderView:  # type: ignore[no-untyped-def]
    """One row, as the domain sees it.

    Unknown statuses become `INITIALIZED` rather than raising. The worker can
    be deployed ahead of this platform, and a status nobody here has heard of
    should render as "not doing anything yet" rather than take down the order
    list.
    """
    try:
        status = OrderStatus(row["status"])
    except ValueError:
        status = OrderStatus.INITIALIZED

    return OrderView(
        broker_order_id=row["broker_order_id"],
        proposal_id=row["proposal_id"],
        symbol=row["symbol"],
        side=Side(row["action"]),
        quantity=int(row["quantity"]),
        status=status,
        filled_quantity=int(row["filled_qty"] or 0),
        average_price=Decimal(str(row["avg_px"] or 0)),
        limit_price=None if row["limit_price"] is None else Decimal(str(row["limit_price"])),
        rationale=row["rationale"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
