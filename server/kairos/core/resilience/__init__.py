"""Failure memory and degradation policy."""

from kairos.core.resilience.breaker import (
    BreakerKey,
    BreakerPolicy,
    CircuitBreaker,
    FallbackPlan,
    State,
    plan_fallbacks,
)

__all__ = [
    "BreakerKey",
    "BreakerPolicy",
    "CircuitBreaker",
    "FallbackPlan",
    "State",
    "plan_fallbacks",
]
