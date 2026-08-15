"""Ambient tenant scope for the current unit of work.

Every request carries a tenant and a user. Passing that identity down through
call signatures means each new call site is another chance to forget it, and
one forgotten argument is an authorization hole rather than a type error. So
the scope lives in a context variable that the API layer establishes once, and
the persistence layer reads without being asked.

The read accessor raises when no scope is set. Returning ``None`` would let a
caller write ``if scope:`` and silently do the unscoped thing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NewType

TenantId = NewType("TenantId", str)
UserId = NewType("UserId", str)


class Role(StrEnum):
    """What a principal may do inside its tenant."""

    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The identity every scoped operation is evaluated against."""

    tenant_id: TenantId
    user_id: UserId
    roles: frozenset[Role] = frozenset()

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def is_service(self) -> bool:
        """Service principals act for a tenant without a human behind them."""
        return Role.SERVICE in self.roles


class ScopeNotEstablished(RuntimeError):
    """Raised when scoped work is attempted outside a tenant scope.

    Reaching this means a code path skipped the middleware that establishes
    scope — a routing bug, not a user error. It must never be caught and
    turned into an empty result set.
    """

    def __init__(self) -> None:
        super().__init__(
            "No tenant scope is active. Scoped operations require a scope "
            "established by the request pipeline or by `scoped()` in tests."
        )


_ACTIVE_SCOPE: Final[ContextVar[TenantScope | None]] = ContextVar(
    "kairos_tenant_scope", default=None
)


def current_scope() -> TenantScope:
    """Return the active scope, or raise if there is none."""
    scope = _ACTIVE_SCOPE.get()
    if scope is None:
        raise ScopeNotEstablished
    return scope


def current_tenant() -> TenantId:
    return current_scope().tenant_id


def current_user() -> UserId:
    return current_scope().user_id


def scope_or_none() -> TenantScope | None:
    """Return the active scope without raising.

    For code that legitimately runs both inside and outside a request — log
    enrichment, metrics tagging. Not for deciding whether to filter a query.
    """
    return _ACTIVE_SCOPE.get()


def establish(scope: TenantScope) -> Token[TenantScope | None]:
    """Bind a scope to the current context.

    Returns the reset token. Callers that cannot use `scoped()` — an ASGI
    middleware spanning a request, say — must pass this to `release()` in a
    finally block, or the scope leaks to whatever runs next on this task.
    """
    return _ACTIVE_SCOPE.set(scope)


def release(token: Token[TenantScope | None]) -> None:
    _ACTIVE_SCOPE.reset(token)


@contextmanager
def scoped(scope: TenantScope) -> Iterator[TenantScope]:
    """Run a block under `scope`, restoring the previous one on exit."""
    token = establish(scope)
    try:
        yield scope
    finally:
        release(token)
