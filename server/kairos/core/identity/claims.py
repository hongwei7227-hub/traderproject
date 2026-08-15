"""What a verified caller looks like, and the port that produces one.

Deliberately small. Everything about *how* a token is checked — a signature, a
Redis lookup, a fixed table in a test — belongs to an implementation. What
belongs here is only the shape of the answer, because that is the part both the
delivery layer and the adapters have to agree on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kairos.core.tenancy import Role


class AuthenticationError(Exception):
    """The caller could not be identified.

    Carries no detail about why. Telling an unauthenticated caller whether a
    token was expired, malformed or simply for the wrong issuer helps them more
    than it helps us.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason  # logged, never returned
        super().__init__("Not authenticated")


@dataclass(frozen=True, slots=True)
class VerifiedClaims:
    """What a verified token asserts.

    `tenant` is optional because not every identity provider knows about
    tenancy. A token proves who someone is; which account they are acting for
    is a separate question, and one this platform answers from its own
    membership records.
    """

    subject: str
    tenant: str | None = None
    roles: frozenset[Role] = frozenset()


class TokenVerifier(Protocol):
    """Verifies a token and returns its claims.

    A protocol rather than a concrete class so that the identity provider is a
    deployment decision, and so that tests need neither a network nor a clock.

    Asynchronous because the deployed verifier reads a session out of Redis.
    Making only that one implementation async would mean two paths through the
    middleware, and the one exercised by tests would not be the one that runs.
    """

    async def verify(self, token: str) -> VerifiedClaims: ...
