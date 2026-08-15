"""Analyst ratings, from the analyst service.

That service owns the caching — a local cache in front of Redis in front of
Postgres, with a bloom filter to keep unknown symbols from reaching the
database at all. None of that is repeated here. A second cache in this process
would be a second thing to invalidate, and the service already broadcasts its
invalidations to its own instances and not to ours.

So this client is thin on purpose: it asks, and it turns the answer into shapes
this platform can render.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from kairos.adapters.services.http import ServiceCall


@dataclass(frozen=True, slots=True)
class Grade:
    """One firm changing its mind."""

    firm: str
    from_grade: str
    to_grade: str
    action: str

    @property
    def is_upgrade(self) -> bool:
        return self.action.lower() == "upgrade"

    @property
    def is_downgrade(self) -> bool:
        return self.action.lower() == "downgrade"


@dataclass(frozen=True, slots=True)
class AnalystRating:
    """What the street thinks of one symbol."""

    symbol: str
    company_name: str = ""
    consensus: str = ""
    target_price: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    analyst_count: int = 0
    recent_grades: tuple[Grade, ...] = ()
    updated_at: datetime | None = None

    @property
    def has_target(self) -> bool:
        return self.target_price is not None

    def upside_from(self, price: float) -> float | None:
        """Distance to the consensus target, as a fraction.

        `None` rather than zero when there is no target or no price: an
        interface showing "0%" would be asserting the stock is fairly valued,
        which is a different statement from having nothing to say.
        """
        if self.target_price is None or price <= 0:
            return None
        return (self.target_price - price) / price


class AnalystClient:
    """Reads ratings. Never writes them."""

    __slots__ = ("_call",)

    def __init__(self, call: ServiceCall) -> None:
        self._call = call

    async def rating(self, symbol: str) -> AnalystRating | None:
        """The rating for a symbol, or None if nobody covers it.

        Uncovered symbols are ordinary — most listed companies have no
        analyst coverage worth the name — so their absence comes back as None
        rather than as an error the caller has to catch.
        """
        body = await self._call.get_optional(f"/stock/{symbol.upper()}/analyst")
        return None if body is None else _rating(body)

    async def cache_stats(self) -> dict[str, int]:
        """The service's own cache counters.

        Exposed because a two-level cache whose hit rate nobody can see is a
        two-level cache nobody can tune.
        """
        body = await self._call.get("/cache/stats")
        return {str(k): int(v) for k, v in dict(body).items()}


def _rating(body: dict) -> AnalystRating:
    """The service's field names, mapped once.

    Its shape is camelCase Java; ours is not. Doing the translation here means
    a rename on that side is a change to this function rather than a hunt
    through every component that renders a rating.
    """
    return AnalystRating(
        symbol=str(body.get("symbol", "")),
        company_name=str(body.get("companyName") or ""),
        consensus=str(body.get("consensusRating") or ""),
        target_price=_number(body.get("targetPriceConsensus")),
        target_high=_number(body.get("targetPriceHigh")),
        target_low=_number(body.get("targetPriceLow")),
        analyst_count=int(body.get("numAnalysts") or 0),
        recent_grades=tuple(_grade(g) for g in body.get("recentGrades") or ()),
        updated_at=_moment(body.get("updatedAt")),
    )


def _grade(body: dict) -> Grade:
    return Grade(
        firm=str(body.get("firm") or ""),
        from_grade=str(body.get("fromGrade") or ""),
        to_grade=str(body.get("toGrade") or ""),
        action=str(body.get("action") or ""),
    )


def _number(value: object) -> float | None:
    """A price, or nothing.

    Nothing stays nothing. Coercing a missing target to 0.0 would put a
    price target of zero in front of a reader.
    """
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _moment(value: object) -> datetime | None:
    """Epoch milliseconds, as the service sends them."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError):
        return None
