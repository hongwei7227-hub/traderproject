"""Getting a queued message onto the broker.

Three implementations of one port, and the choice is a deployment decision:

  `RocketMQPublisher`  — the real thing
  `RecordingPublisher` — writes the message where a person can read it
  `RefusingPublisher`  — fails every send

The second exists because a relay that cannot be run without a broker is a
relay nobody exercises until production. The third exists because the
interesting behaviour of the relay is what it does when sending fails, and
arranging for a real broker to fail on demand is harder than it is worth.
"""

from __future__ import annotations

import json
import logging
from typing import Any

LOG = logging.getLogger("kairos.relay")


class PublishFailed(RuntimeError):
    """The message did not reach the broker.

    The relay treats every one of these the same way — record the attempt,
    leave the row pending, try again next sweep — so there is one exception
    rather than one per cause.
    """


class RecordingPublisher:
    """Logs what would have been sent, and remembers it.

    For running the relay without a broker: development, the demo, and the
    first minutes of a deployment where the queue is not yet reachable. It
    reports success, which is a lie the caller has agreed to — the alternative
    is a relay that cannot be started at all.
    """

    __slots__ = ("sent",)

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], str]] = []

    async def publish(
        self, destination: str, payload: dict, *, ordering_key: str
    ) -> None:
        self.sent.append((destination, dict(payload), ordering_key))
        LOG.info(
            "would publish to %s (key=%s): %s",
            destination,
            ordering_key,
            json.dumps(payload, default=str)[:400],
        )


class RefusingPublisher:
    """Fails every send. For exercising the retry path deliberately."""

    __slots__ = ("reason",)

    def __init__(self, reason: str = "no broker configured") -> None:
        self.reason = reason

    async def publish(
        self, destination: str, payload: dict, *, ordering_key: str
    ) -> None:
        raise PublishFailed(self.reason)


class RocketMQPublisher:
    """Publishes to RocketMQ, in order, per account.

    The client is imported lazily. Making it a module-level import would mean
    every process that touches this package — including the test suite and the
    demo — needs a broker client installed to start, and neither of them sends
    anything.

    Ordering is the whole reason this class exists rather than a generic
    producer: orders for one account must arrive in the order they were made,
    or a cancel can overtake the buy it was cancelling. The account id is the
    key, and it is passed through from the outbox row rather than derived here,
    so that what was queued is what is used.
    """

    __slots__ = ("_endpoint", "_producer", "_topics")

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._producer: Any = None
        self._topics: set[str] = set()

    async def start(self) -> None:
        """Connect. Called once, before the first sweep.

        Separate from construction so that building the object — which the
        composition root does eagerly — does not require the broker to be up.
        A process must be able to start far enough to report that it cannot
        reach its broker.
        """
        if self._producer is not None:
            return

        try:
            from rocketmq.client import Producer  # type: ignore[import-not-found]
        except ImportError as missing:  # pragma: no cover - depends on install
            raise PublishFailed(
                "the RocketMQ client is not installed; install it or run the "
                "relay with --dry-run"
            ) from missing

        producer = Producer("kairos-outbox-relay", orderly=True)
        producer.set_name_server_address(self._endpoint)
        producer.start()
        self._producer = producer

    async def stop(self) -> None:
        if self._producer is not None:
            self._producer.shutdown()
            self._producer = None

    async def publish(
        self, destination: str, payload: dict, *, ordering_key: str
    ) -> None:
        if self._producer is None:
            await self.start()

        topic, _, tag = destination.partition(":")
        try:
            from rocketmq.client import Message  # type: ignore[import-not-found]

            message = Message(topic)
            if tag:
                message.set_tags(tag)
            message.set_keys(ordering_key)
            message.set_body(json.dumps(payload, default=str))
            # Orderly send: the shard is chosen from the key, so every message
            # for one account lands on the same queue and is consumed in order.
            self._producer.send_orderly_with_sharding_key(message, ordering_key)
        except Exception as error:  # noqa: BLE001 - one failure mode for the relay
            raise PublishFailed(f"{type(error).__name__}: {error}") from error
