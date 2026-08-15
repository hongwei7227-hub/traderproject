"""Run the outbox relay.

    python scripts/relay.py                # drain to RocketMQ, until stopped
    python scripts/relay.py --dry-run      # log what would be sent, send nothing
    python scripts/relay.py --once         # one sweep, then exit

`--once` is for deployments that would rather schedule this than supervise it.
`--dry-run` is for seeing what is queued without a broker, which is also how
the relay gets exercised in development rather than first in production.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from kairos.adapters.trading.outbox import MessagePublisher  # noqa: E402
from kairos.adapters.trading.publishers import (  # noqa: E402
    RecordingPublisher,
    RocketMQPublisher,
)
from kairos.runtime.relay import RelayPolicy, RelayService  # noqa: E402
from kairos.runtime.settings import get_settings  # noqa: E402


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log messages instead of publishing them",
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single sweep and exit"
    )
    parser.add_argument(
        "--broker",
        default=None,
        help="RocketMQ name server address; defaults to the configured one",
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-attempts", type=int, default=5)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    options = parse(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )

    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    publisher: MessagePublisher = (
        RecordingPublisher()
        if options.dry_run
        else RocketMQPublisher(options.broker or settings.services.execution_url)
    )

    service = RelayService(
        sessions,
        publisher,
        RelayPolicy(batch_size=options.batch_size, max_attempts=options.max_attempts),
    )

    if options.once:
        sent, failed = await service.sweep_once()
        print(f"{sent} sent, {failed} failed")
        await engine.dispose()
        return 0 if failed == 0 else 1

    _install_signal_handlers(service)
    try:
        await service.run()
    finally:
        await engine.dispose()
    return 0


def _install_signal_handlers(service: RelayService) -> None:
    """Stop on the signals a supervisor sends.

    Asks the loop to finish its sweep rather than cancelling it. A cancellation
    mid-publish leaves a message whose delivery nobody knows the outcome of;
    the row would be retried, which is correct but avoidable.

    Windows has no SIGTERM handler through asyncio, so each is installed
    independently and a missing one is not fatal — the process is still
    stoppable, just less gracefully.
    """
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        received = getattr(signal, name, None)
        if received is None:
            continue
        try:
            loop.add_signal_handler(received, service.stop)
        except NotImplementedError:
            signal.signal(received, lambda *_: service.stop())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
