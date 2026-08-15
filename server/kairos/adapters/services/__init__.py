"""Clients for the services this platform does not implement itself.

Each one owns a capability outright: analyst data, billing. They are reached
over HTTP rather than linked in, so one can be restarted or replaced without
this process knowing — and so that one being down degrades a page rather than
taking the platform with it.
"""

from kairos.adapters.services.analyst import (
    AnalystClient,
    AnalystRating,
    Grade,
)
from kairos.adapters.services.billing import (
    BillingClient,
    Membership,
    Plan,
    RechargeOrder,
)
from kairos.adapters.services.http import (
    ServiceCall,
    ServiceUnavailable,
    Transport,
)

__all__ = [
    "AnalystClient",
    "AnalystRating",
    "BillingClient",
    "Grade",
    "Membership",
    "Plan",
    "RechargeOrder",
    "ServiceCall",
    "ServiceUnavailable",
    "Transport",
]
