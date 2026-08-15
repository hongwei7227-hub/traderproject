"""Trading, as this platform sees it.

The platform does not execute orders. It decides what to propose, hands the
proposal to the execution worker, and reads back what became of it. That split
is why this package is small: the state machine, the fill arithmetic and the
broker conversation all live in the worker, and duplicating any of them here
would create a second opinion about the same order.

What does live here is the part the worker cannot own — whether a proposal is
allowed to be made at all, and what the answer looks like to a reader.
"""

from kairos.core.trading.orders import (
    OrderGateway,
    OrderStatus,
    OrderView,
    Position,
    Side,
    TradeProposal,
)
from kairos.core.trading.risk import (
    AccountState,
    RiskDecision,
    RiskLimits,
    RiskRefusal,
    assess,
)

__all__ = [
    "AccountState",
    "OrderGateway",
    "OrderStatus",
    "OrderView",
    "Position",
    "RiskDecision",
    "RiskLimits",
    "RiskRefusal",
    "Side",
    "TradeProposal",
    "assess",
]
