"""Metering work whose cost is unknown until it is finished.

An ordinary rate limiter counts requests, because requests are interchangeable.
Agent turns are not: one answers in two hundred tokens and the next spends two
hundred thousand across a dozen tool calls. Counting requests would let a
tenant on a generous request allowance spend an unbounded amount.

So the meter counts tokens, and that forces a two-phase protocol. Before the
work starts we do not know its cost, so we reserve pessimistically against the
largest it could be. When it finishes we settle to what it actually was and
return the difference. A tenant that starts many expensive requests at once
finds the allowance already spoken for, which is the property a post-hoc
counter cannot provide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from kairos.core.tenancy.context import TenantId


class Exhaustion(StrEnum):
    """What to do when a tenant's allowance runs out.

    Configurable per tenant because the right answer differs by customer. A
    trial account should stop; a production one would rather degrade than fail;
    an internal one wants a warning and no interruption.
    """

    REJECT = "reject"
    DEGRADE = "degrade"
    ALLOW = "allow"


class QuotaExceeded(Exception):
    """Raised when a reservation cannot be granted and the policy is to reject."""

    def __init__(self, tenant: TenantId, requested: int, remaining: int) -> None:
        self.tenant = tenant
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"tenant {tenant!r} needs {requested} tokens but has {remaining} left"
        )


@dataclass(frozen=True, slots=True)
class Allowance:
    """A tenant's budget for the current period."""

    limit: int
    consumed: int = 0
    reserved: int = 0
    on_exhaustion: Exhaustion = Exhaustion.REJECT

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.consumed < 0 or self.reserved < 0:
            raise ValueError("consumed and reserved cannot be negative")

    @property
    def available(self) -> int:
        """What is left after both spent and in-flight tokens.

        Reserved tokens count against the allowance even though they may never
        be spent. Not counting them is what lets a burst of concurrent requests
        each see a healthy balance and collectively blow through it.
        """
        return max(0, self.limit - self.consumed - self.reserved)

    @property
    def exhausted(self) -> bool:
        return self.available <= 0

    def can_afford(self, tokens: int) -> bool:
        return tokens <= self.available


@dataclass(frozen=True, slots=True)
class Reservation:
    """A claim on part of an allowance, pending settlement."""

    id: UUID
    tenant: TenantId
    tokens: int
    degraded: bool = False

    @classmethod
    def issue(cls, tenant: TenantId, tokens: int, *, degraded: bool = False) -> Self:
        return cls(id=uuid4(), tenant=tenant, tokens=tokens, degraded=degraded)


@dataclass(frozen=True, slots=True)
class Settlement:
    """What a finished piece of work actually cost."""

    reservation: Reservation
    input_tokens: int
    output_tokens: int

    @property
    def actual(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def adjustment(self) -> int:
        """Tokens to return, or to charge beyond the reservation.

        Negative means the work overran its reservation. That is allowed —
        stopping a half-finished turn to enforce a budget wastes everything
        already spent on it — but it must be recorded, because a persistent
        overrun means the estimate is wrong.
        """
        return self.reservation.tokens - self.actual

    @property
    def overran(self) -> bool:
        return self.adjustment < 0


@dataclass(frozen=True, slots=True)
class Estimate:
    """The pessimistic cost of work not yet done."""

    prompt_tokens: int
    max_output_tokens: int
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.multiplier < 1.0:
            # A multiplier below one would under-reserve on purpose, which
            # defeats reserving at all.
            raise ValueError("multiplier cannot reduce the estimate")

    @property
    def worst_case(self) -> int:
        return int((self.prompt_tokens + self.max_output_tokens) * self.multiplier)


@dataclass(slots=True)
class QuotaDecision:
    """The outcome of asking for a reservation."""

    granted: bool
    reservation: Reservation | None = None
    degrade: bool = False
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.granted


class QuotaPolicy:
    """Decides whether work may start, given an allowance and an estimate.

    Pure. Reading and writing the allowance belongs to an adapter; what to do
    about it belongs here, where it can be reasoned about without a database.
    """

    __slots__ = ("_warn_at",)

    def __init__(self, warn_at_fraction: float = 0.9) -> None:
        if not 0.0 < warn_at_fraction <= 1.0:
            raise ValueError("warn_at_fraction must be in (0, 1]")
        self._warn_at = warn_at_fraction

    def evaluate(
        self, tenant: TenantId, allowance: Allowance, estimate: Estimate
    ) -> QuotaDecision:
        needed = estimate.worst_case

        if allowance.can_afford(needed):
            return QuotaDecision(
                granted=True,
                reservation=Reservation.issue(tenant, needed),
                warnings=self._warnings(allowance, needed),
            )

        return self._handle_exhaustion(tenant, allowance, needed)

    def _handle_exhaustion(
        self, tenant: TenantId, allowance: Allowance, needed: int
    ) -> QuotaDecision:
        match allowance.on_exhaustion:
            case Exhaustion.REJECT:
                return QuotaDecision(
                    granted=False,
                    reason=(
                        f"allowance exhausted: {needed} tokens needed, "
                        f"{allowance.available} available"
                    ),
                )

            case Exhaustion.DEGRADE:
                # Reserve what is left rather than refusing. The caller is
                # expected to honour `degrade` by choosing a cheaper model, so
                # the work continues at lower cost instead of stopping.
                return QuotaDecision(
                    granted=True,
                    reservation=Reservation.issue(
                        tenant, allowance.available, degraded=True
                    ),
                    degrade=True,
                    reason="allowance exhausted; continuing on a cheaper model",
                    warnings=["quota exhausted, degraded"],
                )

            case Exhaustion.ALLOW:
                return QuotaDecision(
                    granted=True,
                    reservation=Reservation.issue(tenant, needed),
                    reason="allowance exceeded; permitted by policy",
                    warnings=[f"tenant {tenant} is over its allowance"],
                )

    def _warnings(self, allowance: Allowance, needed: int) -> list[str]:
        projected = allowance.consumed + allowance.reserved + needed
        if projected >= allowance.limit * self._warn_at:
            used = projected / allowance.limit
            return [f"allowance {used:.0%} consumed"]
        return []


def settle(allowance: Allowance, settlement: Settlement) -> Allowance:
    """Apply a settlement, releasing the unspent part of its reservation.

    Returns a new allowance rather than mutating: the durable record is written
    by whoever owns the transaction, and a value that quietly changed under
    them would be harder to reason about than one they must store.
    """
    return Allowance(
        limit=allowance.limit,
        consumed=allowance.consumed + settlement.actual,
        reserved=max(0, allowance.reserved - settlement.reservation.tokens),
        on_exhaustion=allowance.on_exhaustion,
    )


def reserve(allowance: Allowance, reservation: Reservation) -> Allowance:
    """Apply a granted reservation to an allowance."""
    return Allowance(
        limit=allowance.limit,
        consumed=allowance.consumed,
        reserved=allowance.reserved + reservation.tokens,
        on_exhaustion=allowance.on_exhaustion,
    )


def abandon(allowance: Allowance, reservation: Reservation) -> Allowance:
    """Release a reservation for work that never ran.

    Needed on every failure path. A reservation that is never settled and never
    abandoned holds part of the allowance until the period rolls over, so a run
    of crashes would look to the tenant like a quota that shrinks on its own.
    """
    return Allowance(
        limit=allowance.limit,
        consumed=allowance.consumed,
        reserved=max(0, allowance.reserved - reservation.tokens),
        on_exhaustion=allowance.on_exhaustion,
    )
