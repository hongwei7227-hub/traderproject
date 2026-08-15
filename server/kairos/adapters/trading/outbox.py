"""Getting a proposal out of this process.

Two halves. `OutboxGateway` writes the proposal into a table in the same
transaction as the request that produced it. `OutboxRelay` drains that table to
the message broker afterwards.

Splitting them is the point. Publishing inside the request would make placing
an order fail whenever the broker happened to be unreachable, and — worse —
could publish an order whose surrounding transaction then rolled back, leaving
the worker holding an instruction the platform has no record of.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kairos.adapters.persistence.entities import OrderOutbox, OutboxStatus
from kairos.core.tenancy.context import TenantId, current_scope
from kairos.core.trading.orders import TradeProposal

# The worker's destinations. Named here rather than derived because they are a
# contract with another codebase: a typo should be a diff in one line, not a
# message published into a topic nobody consumes.
SUBMIT_DESTINATION = "kairos-order-submit:create"
CANCEL_DESTINATION = "kairos-order-cancel:cancel"


class MessagePublisher(Protocol):
    """Whatever actually reaches the broker.

    A port so the relay can be tested without one. The ordering key is not
    optional: orders for a single account must arrive in the order they were
    made, or a cancel can overtake the buy it was cancelling.
    """

    async def publish(
        self, destination: str, payload: dict, *, ordering_key: str
    ) -> None: ...


class OutboxGateway:
    """Records a proposal for delivery.

    Implements the core's `OrderGateway`. Nothing here talks to a broker, which
    is why submitting an order cannot fail because of one.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(self, proposal: TradeProposal) -> None:
        scope = current_scope()
        if str(proposal.tenant_id) != str(scope.tenant_id):
            # A proposal built for another tenant reaching this call is a bug
            # upstream, and one that would place a real order against the wrong
            # account. Refused rather than silently re-stamped.
            raise ValueError(
                "proposal names a different tenant than the active scope"
            )

        await self._enqueue(
            OrderOutbox(
                tenant_id=str(scope.tenant_id),
                proposal_id=proposal.proposal_id,
                account_id=proposal.account_id,
                destination=SUBMIT_DESTINATION,
                ordering_key=proposal.account_id,
                payload=proposal.to_wire(),
            )
        )

    async def cancel(self, tenant_id: TenantId, broker_order_id: str) -> None:
        scope = current_scope()
        if str(tenant_id) != str(scope.tenant_id):
            raise ValueError("cancel names a different tenant than the active scope")

        # Keyed on the broker's id rather than the proposal's: by the time
        # anything is cancellable the worker has an order, and that is what it
        # will look for.
        await self._enqueue(
            OrderOutbox(
                tenant_id=str(scope.tenant_id),
                proposal_id=_cancel_key(broker_order_id),
                account_id=broker_order_id,
                destination=CANCEL_DESTINATION,
                ordering_key=broker_order_id,
                payload={"brokerOrderId": broker_order_id, "tenantId": str(tenant_id)},
            )
        )

    async def _enqueue(self, row: OrderOutbox) -> None:
        """Add a row, treating a duplicate as success.

        Inside a savepoint. The unique constraint on (tenant, proposal) is how
        a retry collapses into the original, but a constraint violation poisons
        the transaction it happens in — and rolling that back would undo
        everything else the request had done, which for a retried submission
        means losing work that had nothing to do with the duplicate.
        """
        savepoint = await self._session.begin_nested()
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await savepoint.rollback()
        else:
            await savepoint.commit()


def _cancel_key(broker_order_id: str) -> object:
    """A stable identifier for "the cancel of this order".

    Derived from the broker id so that cancelling twice queues one message.
    Written as a UUID5 so it shares a column with proposal ids without needing
    a second one.
    """
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_OID, f"cancel:{broker_order_id}")


@dataclass(frozen=True, slots=True)
class RelayReport:
    """What one sweep did.

    Returned rather than logged so a caller — a scheduler, a test — can decide
    whether the relay is keeping up without parsing output.
    """

    sent: int = 0
    failed: int = 0

    @property
    def moved(self) -> int:
        return self.sent + self.failed


class OutboxRelay:
    """Drains queued proposals to the broker.

    Runs outside the request. Each row is marked sent only after the publish
    returns, so a crash mid-sweep re-delivers rather than drops — the worker's
    idempotency key is what makes that safe, and it is why the key is generated
    before the proposal is queued rather than at publish time.
    """

    __slots__ = ("_session", "_publisher", "_batch", "_max_attempts")

    def __init__(
        self,
        session: AsyncSession,
        publisher: MessagePublisher,
        *,
        batch_size: int = 50,
        max_attempts: int = 5,
    ) -> None:
        self._session = session
        self._publisher = publisher
        self._batch = batch_size
        self._max_attempts = max_attempts

    async def sweep(self) -> RelayReport:
        rows = await self._pending()
        sent = failed = 0

        for row in rows:
            try:
                await self._publisher.publish(
                    row.destination, dict(row.payload), ordering_key=row.ordering_key
                )
            except Exception as error:  # noqa: BLE001 - recorded, then retried
                await self._record_failure(row, error)
                failed += 1
                continue

            row.status = OutboxStatus.SENT
            row.sent_at = datetime.now(UTC)
            sent += 1

        await self._session.flush()
        return RelayReport(sent=sent, failed=failed)

    async def _pending(self) -> Sequence[OrderOutbox]:
        # Unscoped on purpose: the relay is not serving a tenant, it is
        # draining a queue across all of them. Written as an explicit query
        # rather than through the scoped repository so that the absence of a
        # tenant predicate is visible here instead of looking like an omission.
        result = await self._session.execute(
            select(OrderOutbox)
            .where(OrderOutbox.status == OutboxStatus.PENDING)
            .order_by(OrderOutbox.created_at)
            .limit(self._batch)
        )
        return result.scalars().all()

    async def _record_failure(self, row: OrderOutbox, error: Exception) -> None:
        row.attempts += 1
        row.last_error = f"{type(error).__name__}: {error}"[:1000]
        if row.attempts >= self._max_attempts:
            # Stops here rather than retrying forever. A proposal that has
            # failed five times is not going to succeed on the sixth, and a
            # queue that never drains hides every later order behind it.
            row.status = OutboxStatus.FAILED

    async def abandon(self, tenant_id: TenantId, proposal_id: object) -> None:
        """Give up on one row without touching the rest of the queue."""
        await self._session.execute(
            update(OrderOutbox)
            .where(
                OrderOutbox.tenant_id == str(tenant_id),
                OrderOutbox.proposal_id == proposal_id,
                OrderOutbox.status == OutboxStatus.PENDING,
            )
            .values(status=OutboxStatus.FAILED, last_error="abandoned")
        )
