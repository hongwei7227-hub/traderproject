"""Remembering which providers are currently failing.

The reference implementation retried and fell back without keeping any record.
A provider that was down stayed in every request's path: each new request spent
its full retry budget rediscovering the outage before falling back. Under a
sustained failure the platform generated more load against the broken provider
than against the healthy one.

A breaker keeps that memory. The subtlety is what to key it on. Keyed globally,
one tenant's exhausted quota trips the breaker for everyone — the noisy
neighbour problem, arriving through the failure path rather than the load path.
Keyed by provider alone, the same. Keyed by (tenant, provider), a tenant's
credential and quota problems stay theirs, while a genuine provider outage
trips independently for each tenant that touches it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from typing import Final, NamedTuple

from kairos.core.catalog.descriptors import ProviderId
from kairos.core.tenancy.context import TenantId


class BreakerKey(NamedTuple):
    """What a breaker's memory is scoped to."""

    tenant: TenantId
    provider: ProviderId


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    """When to open, how long to stay open, and how to close again."""

    failure_threshold: int = 5
    recovery_after_seconds: float = 60.0
    success_threshold: int = 2
    half_open_max_calls: int = 1

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be at least 1")
        if self.recovery_after_seconds <= 0:
            raise ValueError("recovery_after_seconds must be positive")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be at least 1")


@dataclass(slots=True)
class _Circuit:
    """Mutable state for one key. Guarded by the registry's lock."""

    state: State = State.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: float | None = None
    probes_in_flight: int = 0


Clock = Callable[[], float]


class CircuitBreaker:
    """Per-(tenant, provider) failure memory.

    Deliberately not a decorator. Callers ask `allows()` before attempting a
    provider and report the outcome afterwards, because the interesting
    decision — which provider to try next — belongs to the fallback chain, not
    to the breaker.
    """

    __slots__ = ("_policy", "_clock", "_circuits", "_lock")

    def __init__(
        self,
        policy: BreakerPolicy | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._policy: Final = policy or BreakerPolicy()
        # Injected so tests can advance time without sleeping, and monotonic by
        # default so a wall-clock adjustment cannot strand a circuit open.
        self._clock: Final = clock
        self._circuits: dict[BreakerKey, _Circuit] = {}
        self._lock = Lock()

    # -- queries -----------------------------------------------------------

    def allows(self, key: BreakerKey) -> bool:
        """Whether a call to this provider should be attempted.

        Transitions an expired open circuit to half-open and admits a limited
        number of probes, so recovery costs one request rather than a surge.
        """
        with self._lock:
            circuit = self._circuits.get(key)
            if circuit is None or circuit.state is State.CLOSED:
                return True

            if circuit.state is State.OPEN:
                if not self._recovery_due(circuit):
                    return False
                circuit.state = State.HALF_OPEN
                circuit.consecutive_successes = 0
                circuit.probes_in_flight = 1
                return True

            # Half-open: admit up to the probe limit, refuse the rest. Without
            # this cap every concurrent request would probe at once, which is
            # the stampede the breaker exists to prevent.
            if circuit.probes_in_flight < self._policy.half_open_max_calls:
                circuit.probes_in_flight += 1
                return True
            return False

    def state_of(self, key: BreakerKey) -> State:
        with self._lock:
            circuit = self._circuits.get(key)
            if circuit is None:
                return State.CLOSED
            if circuit.state is State.OPEN and self._recovery_due(circuit):
                return State.HALF_OPEN
            return circuit.state

    def is_open(self, key: BreakerKey) -> bool:
        return self.state_of(key) is State.OPEN

    # -- outcome reporting -------------------------------------------------

    def record_success(self, key: BreakerKey) -> None:
        with self._lock:
            circuit = self._circuits.get(key)
            if circuit is None:
                return

            if circuit.state is State.HALF_OPEN:
                circuit.probes_in_flight = max(0, circuit.probes_in_flight - 1)
                circuit.consecutive_successes += 1
                if circuit.consecutive_successes >= self._policy.success_threshold:
                    self._circuits.pop(key, None)
                return

            # A success in the closed state clears accumulated failures:
            # the threshold counts consecutive failures, not lifetime ones.
            circuit.consecutive_failures = 0

    def record_failure(self, key: BreakerKey) -> None:
        with self._lock:
            circuit = self._circuits.setdefault(key, _Circuit())

            if circuit.state is State.HALF_OPEN:
                # The probe failed. Straight back to open with a fresh timer,
                # rather than counting toward the threshold again.
                circuit.state = State.OPEN
                circuit.opened_at = self._clock()
                circuit.consecutive_successes = 0
                circuit.probes_in_flight = 0
                return

            circuit.consecutive_failures += 1
            circuit.consecutive_successes = 0
            if circuit.consecutive_failures >= self._policy.failure_threshold:
                circuit.state = State.OPEN
                circuit.opened_at = self._clock()

    # -- administration ----------------------------------------------------

    def reset(self, key: BreakerKey) -> None:
        """Forget one circuit. For operator intervention after a known fix."""
        with self._lock:
            self._circuits.pop(key, None)

    def open_circuits(self) -> tuple[BreakerKey, ...]:
        """Currently-open circuits, for health reporting."""
        with self._lock:
            return tuple(
                key
                for key, circuit in self._circuits.items()
                if circuit.state is State.OPEN and not self._recovery_due(circuit)
            )

    # -- internals ---------------------------------------------------------

    def _recovery_due(self, circuit: _Circuit) -> bool:
        if circuit.opened_at is None:
            return True
        return (self._clock() - circuit.opened_at) >= self._policy.recovery_after_seconds


@dataclass(slots=True)
class FallbackPlan:
    """The providers still worth trying, in order.

    Produced by consulting the breaker rather than by the caller checking each
    provider itself, so that "skip what is known broken" is one decision made
    in one place.
    """

    candidates: tuple[ProviderId, ...]
    skipped: tuple[ProviderId, ...] = field(default_factory=tuple)

    @property
    def exhausted(self) -> bool:
        return not self.candidates


def plan_fallbacks(
    breaker: CircuitBreaker,
    tenant: TenantId,
    providers: tuple[ProviderId, ...],
) -> FallbackPlan:
    """Filter a fallback chain down to providers the breaker still allows.

    If every provider is open the plan keeps the first one anyway: refusing to
    call anything turns a degraded service into an outage, and a single request
    against a recovering provider is cheaper than telling the tenant no.
    """
    allowed: list[ProviderId] = []
    skipped: list[ProviderId] = []
    for provider in providers:
        if breaker.allows(BreakerKey(tenant, provider)):
            allowed.append(provider)
        else:
            skipped.append(provider)

    if not allowed and providers:
        return FallbackPlan(candidates=(providers[0],), skipped=tuple(providers[1:]))
    return FallbackPlan(candidates=tuple(allowed), skipped=tuple(skipped))
