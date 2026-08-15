"""The process that drains the outbox.

A request commits a proposal to a table and returns. This is what carries it
the rest of the way. It runs outside any request, which is the point: an order
must survive the broker being down, the relay being restarted, and the web
process being replaced under it.

Three properties it owes:

  at-least-once   a row is marked sent only after the publish returns, so a
                  crash mid-sweep re-delivers rather than drops
  in order        per account, because a cancel must not overtake its buy
  bounded         it gives up on a row that has failed enough times, so a
                  queue that cannot drain does not hide every order behind it

The first is why the worker's idempotency key is generated before the proposal
is queued: at-least-once is only safe if a duplicate is harmless.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from kairos.adapters.trading.outbox import MessagePublisher, OutboxRelay

LOG = logging.getLogger("kairos.relay")


class Lifecycle(Protocol):
    """A publisher that holds a connection.

    Optional: a publisher without these is used as-is. Declaring it as a
    protocol rather than requiring it keeps the simple implementations simple.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RelayPolicy:
    """How hard to work, and how long to wait when there is nothing to do."""

    batch_size: int = 50
    max_attempts: int = 5

    # Two intervals rather than one. A queue with work in it should be drained
    # as fast as the broker allows; an idle queue should not be a database
    # query every hundred milliseconds for the rest of the deployment's life.
    busy_interval_seconds: float = 0.1
    idle_interval_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.busy_interval_seconds > self.idle_interval_seconds:
            raise ValueError(
                "busy interval exceeds idle interval; the relay would slow "
                "down exactly when there is work to do"
            )


@dataclass(frozen=True, slots=True)
class RelayStats:
    sweeps: int = 0
    sent: int = 0
    failed: int = 0

    def after(self, sent: int, failed: int) -> RelayStats:
        return RelayStats(
            sweeps=self.sweeps + 1, sent=self.sent + sent, failed=self.failed + failed
        )


class RelayService:
    """Runs sweeps until asked to stop.

    Each sweep gets its own session and commits. Holding one open across the
    life of the process would keep a transaction — and, with row locking, the
    locks it took — alive for as long as the relay ran.
    """

    __slots__ = ("_sessions", "_publisher", "_policy", "_stop", "_stats")

    def __init__(
        self,
        sessions: async_sessionmaker,
        publisher: MessagePublisher,
        policy: RelayPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher
        self._policy = policy or RelayPolicy()
        self._stop = asyncio.Event()
        self._stats = RelayStats()

    @property
    def stats(self) -> RelayStats:
        return self._stats

    def stop(self) -> None:
        """Ask the loop to finish the sweep it is on and return.

        Not a cancellation. Cancelling mid-publish would leave a message whose
        delivery nobody knows the outcome of, and the row still pending — which
        is recoverable, but a clean shutdown should not need recovering from.
        """
        self._stop.set()

    async def sweep_once(self) -> tuple[int, int]:
        """One pass. Returns what moved.

        Exposed separately so a deployment can run the relay as a scheduled job
        instead of a daemon, and so a test can drive it without a loop.
        """
        async with self._sessions() as session:
            relay = OutboxRelay(
                session,
                self._publisher,
                batch_size=self._policy.batch_size,
                max_attempts=self._policy.max_attempts,
            )
            report = await relay.sweep()
            # Committed here rather than inside the relay: the relay describes
            # what a sweep does, and whether that is a transaction of its own
            # is the caller's decision.
            await session.commit()

        self._stats = self._stats.after(report.sent, report.failed)
        return report.sent, report.failed

    async def run(self) -> RelayStats:
        """Sweep until stopped."""
        await self._start_publisher()
        LOG.info("relay started")

        try:
            while not self._stop.is_set():
                try:
                    sent, failed = await self.sweep_once()
                except Exception:  # noqa: BLE001 - a sweep must not kill the loop
                    # A database blip should cost one sweep, not the process.
                    # Logged with the traceback because unlike a publish
                    # failure — which is recorded on the row — this one leaves
                    # no trace anywhere else.
                    LOG.exception("sweep failed")
                    sent = failed = 0

                if sent or failed:
                    LOG.info("swept: %d sent, %d failed", sent, failed)

                delay = (
                    self._policy.busy_interval_seconds
                    if sent
                    else self._policy.idle_interval_seconds
                )
                # Waiting on the event rather than sleeping, so a stop during
                # an idle interval takes effect immediately instead of after
                # the full delay.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            await self._stop_publisher()
            LOG.info(
                "relay stopped after %d sweeps: %d sent, %d failed",
                self._stats.sweeps,
                self._stats.sent,
                self._stats.failed,
            )

        return self._stats

    async def _start_publisher(self) -> None:
        start = getattr(self._publisher, "start", None)
        if callable(start):
            await start()

    async def _stop_publisher(self) -> None:
        stop = getattr(self._publisher, "stop", None)
        if callable(stop):
            try:
                await stop()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                LOG.exception("publisher did not shut down cleanly")
