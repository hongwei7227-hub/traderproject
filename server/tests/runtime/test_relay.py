"""The process that drains the outbox.

Its correctness is entirely about failure and shutdown. A sweep that works is
one line; what matters is that a crash re-delivers rather than drops, that a
database blip costs one sweep rather than the process, and that stopping does
not abandon a publish halfway.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kairos.adapters.persistence.entities import Base, OrderOutbox, OutboxStatus, Tenant
from kairos.adapters.trading.outbox import OutboxGateway
from kairos.adapters.trading.publishers import (
    PublishFailed,
    RecordingPublisher,
    RefusingPublisher,
)
from kairos.core.tenancy import Role, TenantId, TenantScope, UserId, scoped
from kairos.core.trading import Side, TradeProposal
from kairos.runtime.relay import RelayPolicy, RelayService
from sqlalchemy import select

ACME = TenantScope(
    tenant_id=TenantId("acme"), user_id=UserId("alice"), roles=frozenset({Role.MEMBER})
)

# Fast enough that the loop tests do not add seconds to the suite, and still
# ordered busy < idle so the policy's own invariant holds.
BRISK = RelayPolicy(busy_interval_seconds=0.001, idle_interval_seconds=0.01)


@pytest_asyncio.fixture
async def sessions():  # type: ignore[no-untyped-def]
    """A session factory over a file-free database.

    A factory rather than a session, because the relay opens one per sweep —
    holding a single transaction open for the life of the process would keep
    its locks alive just as long.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup:
        setup.add(Tenant(id="acme", display_name="Acme"))
        await setup.commit()

    yield maker
    await engine.dispose()


async def queue(maker, count: int = 1) -> None:  # type: ignore[no-untyped-def]
    # `scoped` is a plain context manager, so it nests inside rather than
    # sitting beside the async one.
    async with maker() as session:
        with scoped(ACME):
            gateway = OutboxGateway(session)
            for index in range(count):
                await gateway.submit(
                    TradeProposal(
                        tenant_id=ACME.tenant_id,
                        account_id="paper",
                        symbol=f"SYM{index}",
                        side=Side.BUY,
                        quantity=1,
                        limit_price=Decimal("10"),
                    )
                )
        await session.commit()


async def rows(maker) -> list[OrderOutbox]:  # type: ignore[no-untyped-def]
    async with maker() as session:
        result = await session.execute(select(OrderOutbox))
        return list(result.scalars())


class TestPolicy:
    def test_a_busy_interval_slower_than_idle_is_refused(self) -> None:
        """It would slow the relay down exactly when there is work to do."""
        with pytest.raises(ValueError, match="busy interval"):
            RelayPolicy(busy_interval_seconds=5, idle_interval_seconds=1)


class TestSweeping:
    async def test_a_sweep_publishes_and_commits(self, sessions) -> None:  # type: ignore[no-untyped-def]
        await queue(sessions, 2)
        publisher = RecordingPublisher()

        sent, failed = await RelayService(sessions, publisher, BRISK).sweep_once()

        assert (sent, failed) == (2, 0)
        assert len(publisher.sent) == 2
        assert all(r.status == OutboxStatus.SENT for r in await rows(sessions))

    async def test_the_commit_survives_the_session(self, sessions) -> None:  # type: ignore[no-untyped-def]
        """Each sweep is its own transaction. Without the commit the rows would
        be marked sent in memory and read back as pending."""
        await queue(sessions)
        await RelayService(sessions, RecordingPublisher(), BRISK).sweep_once()

        assert (await rows(sessions))[0].status == OutboxStatus.SENT

    async def test_an_empty_queue_is_not_an_error(self, sessions) -> None:  # type: ignore[no-untyped-def]
        assert await RelayService(sessions, RecordingPublisher(), BRISK).sweep_once() == (0, 0)

    async def test_the_ordering_key_reaches_the_publisher(self, sessions) -> None:  # type: ignore[no-untyped-def]
        """Orders for one account must arrive in order, or a cancel can
        overtake the buy it was cancelling."""
        await queue(sessions)
        publisher = RecordingPublisher()
        await RelayService(sessions, publisher, BRISK).sweep_once()

        assert publisher.sent[0][2] == "paper"

    async def test_a_batch_bounds_one_sweep(self, sessions) -> None:  # type: ignore[no-untyped-def]
        await queue(sessions, 5)
        policy = RelayPolicy(batch_size=2, busy_interval_seconds=0.001)

        sent, _ = await RelayService(sessions, RecordingPublisher(), policy).sweep_once()

        assert sent == 2


class TestFailure:
    async def test_a_failed_publish_leaves_the_row_for_next_time(self, sessions) -> None:  # type: ignore[no-untyped-def]
        """At-least-once. The worker's idempotency key is what makes
        re-delivery safe, which is why marking sent happens after the publish
        returns rather than before."""
        await queue(sessions)

        sent, failed = await RelayService(sessions, RefusingPublisher(), BRISK).sweep_once()

        assert (sent, failed) == (0, 1)
        row = (await rows(sessions))[0]
        assert row.status == OutboxStatus.PENDING
        assert row.attempts == 1

    async def test_the_reason_is_recorded_on_the_row(self, sessions) -> None:  # type: ignore[no-untyped-def]
        await queue(sessions)
        await RelayService(sessions, RefusingPublisher("broker asleep"), BRISK).sweep_once()

        assert "broker asleep" in ((await rows(sessions))[0].last_error or "")

    async def test_it_gives_up_after_enough_attempts(self, sessions) -> None:  # type: ignore[no-untyped-def]
        """A queue that cannot drain would otherwise hide every later order
        behind the one that is stuck."""
        await queue(sessions)
        service = RelayService(
            sessions, RefusingPublisher(), RelayPolicy(max_attempts=2, busy_interval_seconds=0.001)
        )

        await service.sweep_once()
        await service.sweep_once()

        assert (await rows(sessions))[0].status == OutboxStatus.FAILED

    async def test_a_failing_row_does_not_block_a_healthy_one(self, sessions) -> None:  # type: ignore[no-untyped-def]
        await queue(sessions, 3)
        publisher = FailsOne(fail_on=1)

        sent, failed = await RelayService(sessions, publisher, BRISK).sweep_once()

        assert (sent, failed) == (2, 1)


class FailsOne:
    """Fails the nth publish of each sweep and succeeds at the rest."""

    def __init__(self, fail_on: int) -> None:
        self._fail_on = fail_on
        self._seen = 0

    async def publish(self, destination: str, payload: dict, *, ordering_key: str) -> None:
        index = self._seen
        self._seen += 1
        if index == self._fail_on:
            raise PublishFailed("this one")


class ExplodingSessions:
    """A session factory that fails, to stand in for a database blip."""

    def __call__(self):  # type: ignore[no-untyped-def]
        raise ConnectionError("database went away")


class TestLoop:
    async def test_it_drains_and_can_be_stopped(self, sessions) -> None:  # type: ignore[no-untyped-def]
        await queue(sessions, 3)
        publisher = RecordingPublisher()
        service = RelayService(sessions, publisher, BRISK)

        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.15)
        service.stop()
        stats = await asyncio.wait_for(task, timeout=2)

        assert stats.sent == 3
        assert len(publisher.sent) == 3

    async def test_stopping_while_idle_takes_effect_at_once(self, sessions) -> None:  # type: ignore[no-untyped-def]
        """It waits on the stop event rather than sleeping, so a stop during an
        idle interval does not have to wait the interval out."""
        service = RelayService(
            sessions,
            RecordingPublisher(),
            RelayPolicy(busy_interval_seconds=0.001, idle_interval_seconds=30),
        )

        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.05)
        service.stop()

        # Would time out if the loop were sleeping out its 30-second interval.
        await asyncio.wait_for(task, timeout=2)

    async def test_a_database_failure_costs_one_sweep_not_the_process(self) -> None:
        """Logged and continued. A blip should not require a supervisor to
        notice the process died and restart it."""
        service = RelayService(ExplodingSessions(), RecordingPublisher(), BRISK)  # type: ignore[arg-type]

        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.08)
        service.stop()
        stats = await asyncio.wait_for(task, timeout=2)

        # Still running, still counting sweeps, nothing sent.
        assert stats.sweeps == 0
        assert stats.sent == 0

    async def test_a_publisher_with_a_lifecycle_is_started_and_stopped(
        self, sessions
    ) -> None:  # type: ignore[no-untyped-def]
        publisher = Lifecycled()
        service = RelayService(sessions, publisher, BRISK)

        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.03)
        service.stop()
        await asyncio.wait_for(task, timeout=2)

        assert publisher.started and publisher.stopped

    async def test_a_publisher_that_will_not_shut_down_does_not_hang_the_exit(
        self, sessions
    ) -> None:  # type: ignore[no-untyped-def]
        """Shutdown must not raise. A broker client failing to close is not a
        reason for the process to exit non-zero."""
        publisher = Lifecycled(stop_raises=True)
        service = RelayService(sessions, publisher, BRISK)

        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.03)
        service.stop()

        await asyncio.wait_for(task, timeout=2)


class Lifecycled(RecordingPublisher):
    def __init__(self, stop_raises: bool = False) -> None:
        super().__init__()
        self.started = False
        self.stopped = False
        self._stop_raises = stop_raises

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        if self._stop_raises:
            raise RuntimeError("client would not close")
