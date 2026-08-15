"""Proposals leaving, orders coming back.

The outbox exists so that placing an order and recording that it was placed
cannot disagree. So the tests worth having are the ones where they could: a
duplicate submission, a broker that is down, and a relay that dies halfway
through a sweep.

The orders table belongs to the execution worker. Here it is created by hand
with no schema qualifier, because SQLite has none — everything else about the
queries is what runs in production.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from kairos.adapters.persistence.entities import OrderOutbox, OutboxStatus
from kairos.adapters.trading.orders import ExecutionOrders, order_table
from kairos.adapters.trading.outbox import (
    CANCEL_DESTINATION,
    SUBMIT_DESTINATION,
    OutboxGateway,
    OutboxRelay,
)
from kairos.core.tenancy import TenantScope, scoped
from kairos.core.trading import OrderStatus, Side, TradeProposal
from sqlalchemy import select


@pytest_asyncio.fixture
async def orders_table(session: AsyncSession):  # type: ignore[no-untyped-def]
    """The worker's table, unqualified so SQLite can hold it."""
    table = order_table(schema=None)
    connection = await session.connection()
    await connection.run_sync(table.create, checkfirst=True)
    return table


def proposal(scope: TenantScope, **overrides: object) -> TradeProposal:
    base: dict[str, object] = {
        "tenant_id": scope.tenant_id,
        "account_id": "paper",
        "symbol": "NVDA",
        "side": Side.BUY,
        "quantity": 10,
        "limit_price": Decimal("100"),
        "rationale": "earnings beat",
    }
    return TradeProposal(**(base | overrides))  # type: ignore[arg-type]


async def queued(session: AsyncSession) -> list[OrderOutbox]:
    result = await session.execute(select(OrderOutbox).order_by(OrderOutbox.created_at))
    return list(result.scalars())


class TestSubmitting:
    async def test_a_proposal_is_queued_rather_than_published(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """Nothing here talks to a broker, so a broker being down cannot fail
        a submission."""
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))

        rows = await queued(session)
        assert len(rows) == 1
        assert rows[0].destination == SUBMIT_DESTINATION
        assert rows[0].status == OutboxStatus.PENDING

    async def test_the_ordering_key_is_the_account(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """Orders for one account must arrive in order, or a cancel can
        overtake the buy it was cancelling."""
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme, account_id="live"))
        assert (await queued(session))[0].ordering_key == "live"

    async def test_the_payload_is_the_workers_shape(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))
        payload = (await queued(session))[0].payload
        assert payload["action"] == "BUY"
        assert payload["limitPrice"] == "100"
        assert payload["rationale"] == "earnings beat"

    async def test_resubmitting_the_same_proposal_queues_one_message(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """A retry should be a no-op, not a second order.

        The idempotency key is generated before the proposal is queued, which
        is what makes the retry carry the same one.
        """
        again = proposal(acme)
        with scoped(acme):
            gateway = OutboxGateway(session)
            await gateway.submit(again)
            await gateway.submit(again)

        assert len(await queued(session)) == 1

    async def test_a_proposal_for_another_tenant_is_refused(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        """It would place a real order against the wrong account.

        Refused rather than silently re-stamped with the active tenant, which
        would turn a bug upstream into a trade.
        """
        with scoped(acme), pytest.raises(ValueError, match="different tenant"):
            await OutboxGateway(session).submit(proposal(rival))

    async def test_a_cancel_is_queued_to_its_own_destination(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            await OutboxGateway(session).cancel(acme.tenant_id, "ib-1")
        assert (await queued(session))[0].destination == CANCEL_DESTINATION

    async def test_cancelling_twice_queues_one_message(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            gateway = OutboxGateway(session)
            await gateway.cancel(acme.tenant_id, "ib-1")
            await gateway.cancel(acme.tenant_id, "ib-1")
        assert len(await queued(session)) == 1


class FakePublisher:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[str, dict, str]] = []
        self.fail_with = fail_with

    async def publish(self, destination: str, payload: dict, *, ordering_key: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.published.append((destination, payload, ordering_key))


class TestRelay:
    async def test_a_sweep_publishes_and_marks_sent(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))

        publisher = FakePublisher()
        report = await OutboxRelay(session, publisher).sweep()

        assert report.sent == 1
        assert publisher.published[0][0] == SUBMIT_DESTINATION
        assert (await queued(session))[0].status == OutboxStatus.SENT

    async def test_a_sent_row_is_not_published_twice(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))

        publisher = FakePublisher()
        relay = OutboxRelay(session, publisher)
        await relay.sweep()
        await relay.sweep()

        assert len(publisher.published) == 1

    async def test_a_failed_publish_leaves_the_row_pending(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """A crash mid-sweep must re-deliver rather than drop.

        The worker's idempotency key is what makes re-delivery safe, which is
        why marking sent happens after the publish returns and not before.
        """
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))

        report = await OutboxRelay(session, FakePublisher(ConnectionError("down"))).sweep()

        assert report.failed == 1
        row = (await queued(session))[0]
        assert row.status == OutboxStatus.PENDING
        assert row.attempts == 1
        assert "ConnectionError" in (row.last_error or "")

    async def test_it_gives_up_after_enough_attempts(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """A queue that never drains hides every later order behind it."""
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))

        relay = OutboxRelay(session, FakePublisher(ConnectionError("down")), max_attempts=2)
        await relay.sweep()
        await relay.sweep()

        assert (await queued(session))[0].status == OutboxStatus.FAILED

    async def test_it_drains_every_tenant(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        """The relay is not serving a tenant; it is draining a queue.

        Scoping it would leave every other tenant's orders sitting there.
        """
        with scoped(acme):
            await OutboxGateway(session).submit(proposal(acme))
        with scoped(rival):
            await OutboxGateway(session).submit(proposal(rival))

        report = await OutboxRelay(session, FakePublisher()).sweep()
        assert report.sent == 2


async def place(
    session: AsyncSession, table, tenant: str, **overrides: object
) -> None:  # type: ignore[no-untyped-def]
    """Write a row the way the worker would."""
    row: dict[str, object] = {
        "broker_order_id": str(uuid.uuid4()),
        "tenant_id": tenant,
        "proposal_id": str(uuid.uuid4()),
        "symbol": "NVDA",
        "action": "BUY",
        "quantity": 10,
        "limit_price": Decimal("100"),
        "status": "FILLED",
        "filled_qty": 10,
        "avg_px": Decimal("100"),
        "rationale": "",
    }
    await session.execute(table.insert().values(**(row | overrides)))


class TestReadingOrders:
    async def test_a_tenant_sees_only_its_own_orders(
        self, session: AsyncSession, orders_table, acme: TenantScope, rival: TenantScope
    ) -> None:
        """The table being another service's does not make isolation its
        problem. This process is the one exposing it over HTTP."""
        await place(session, orders_table, "acme")
        await place(session, orders_table, "rival")

        with scoped(rival):
            orders = await ExecutionOrders(session, schema=None).recent()

        assert len(orders) == 1

    async def test_working_orders_exclude_finished_ones(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", status="FILLED")
        await place(session, orders_table, "acme", status="ACCEPTED", filled_qty=0)

        with scoped(acme):
            working = await ExecutionOrders(session, schema=None).working()

        assert [o.status for o in working] == [OrderStatus.ACCEPTED]

    async def test_an_unknown_status_does_not_break_the_list(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        """The worker can be deployed ahead of this platform.

        A status nobody here has heard of should render as "not doing anything
        yet" rather than take down the order list.
        """
        await place(session, orders_table, "acme", status="SOMETHING_NEW")

        with scoped(acme):
            orders = await ExecutionOrders(session, schema=None).recent()

        assert orders[0].status is OrderStatus.INITIALIZED

    async def test_an_order_is_findable_by_the_proposal_that_made_it(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        """The join from "what I asked for" to "what happened".

        The outbox knows the proposal id and nothing else; the broker id does
        not exist until the worker has been to the broker.
        """
        await place(session, orders_table, "acme", proposal_id="p-42")

        with scoped(acme):
            found = await ExecutionOrders(session, schema=None).by_proposal("p-42")

        assert found is not None
        assert found.proposal_id == "p-42"

    async def test_another_tenants_order_is_not_findable_by_id(
        self, session: AsyncSession, orders_table, acme: TenantScope, rival: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", broker_order_id="ib-1")

        with scoped(rival):
            assert await ExecutionOrders(session, schema=None).by_broker_id("ib-1") is None


class TestPositions:
    async def test_buys_accumulate(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("100"))
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("120"))

        with scoped(acme):
            positions = await ExecutionOrders(session, schema=None).positions()

        assert positions[0].quantity == 20
        assert positions[0].average_cost == Decimal("110")

    async def test_a_sale_reduces_the_holding(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("100"))
        await place(
            session, orders_table, "acme", action="SELL", filled_qty=4, avg_px=Decimal("130")
        )

        with scoped(acme):
            positions = await ExecutionOrders(session, schema=None).positions()

        assert positions[0].quantity == 6

    async def test_a_sale_does_not_move_the_cost_basis(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        """Selling realises profit or loss; it does not change what the rest
        cost. Recomputing the average on a sell makes the basis drift."""
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("100"))
        await place(
            session, orders_table, "acme", action="SELL", filled_qty=4, avg_px=Decimal("130")
        )

        with scoped(acme):
            positions = await ExecutionOrders(session, schema=None).positions()

        assert positions[0].average_cost == Decimal("100")

    async def test_a_fully_sold_holding_disappears(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("100"))
        await place(
            session, orders_table, "acme", action="SELL", filled_qty=10, avg_px=Decimal("130")
        )

        with scoped(acme):
            assert await ExecutionOrders(session, schema=None).positions() == []

    async def test_unfilled_orders_do_not_create_positions(
        self, session: AsyncSession, orders_table, acme: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", status="ACCEPTED", filled_qty=0)

        with scoped(acme):
            assert await ExecutionOrders(session, schema=None).positions() == []

    async def test_positions_are_per_tenant(
        self, session: AsyncSession, orders_table, acme: TenantScope, rival: TenantScope
    ) -> None:
        await place(session, orders_table, "acme", filled_qty=10, avg_px=Decimal("100"))
        await place(session, orders_table, "rival", filled_qty=99, avg_px=Decimal("100"))

        with scoped(acme):
            positions = await ExecutionOrders(session, schema=None).positions()

        assert positions[0].quantity == 10
