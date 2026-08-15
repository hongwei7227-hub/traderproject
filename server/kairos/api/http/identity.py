"""Establishing who is calling, before anything else runs.

Three routes in, in descending order of trust:

  solo mode     — nobody is authenticated because nobody else can reach it
  bearer token  — a verified token from the configured issuer
  service token — a shared secret, which may act for any tenant

The third exists because internal services need to act on a tenant's behalf.
It is also the most dangerous, so it is checked with a constant-time
comparison, requires the tenant to be named explicitly, and is refused
entirely unless a secret has been configured.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kairos.core.identity import AuthenticationError, TokenVerifier, VerifiedClaims
from kairos.core.tenancy import Role, TenantId, TenantScope, UserId
from kairos.runtime.settings import AuthSettings, Deployment, Settings

# Re-exported: these are the vocabulary of this module's callers, and moving
# them to the core should not have made every route import from two places.
__all__ = [
    "AuthenticationError",
    "ClaimsMapper",
    "IdentityResolver",
    "StaticVerifier",
    "TokenVerifier",
    "VerifiedClaims",
    "bare_token",
]


def bare_token(authorization: str) -> str:
    """The token itself, with or without a scheme in front of it.

    Both shapes arrive in practice. A browser talking to this platform sends
    `Bearer <token>`; the login service's own interceptor reads the header raw
    and its clients send the token alone. Refusing the second would mean a
    token that works against one half of the system and not the other, which
    reads to a user as being logged in and logged out at the same time.

    Only `Bearer` is stripped. Silently accepting `Basic <base64>` would treat
    an encoded password as an opaque token and look it up as a session.
    """
    scheme, separator, rest = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        token = rest.strip()
    elif separator:
        raise AuthenticationError(f"unsupported authorization scheme {scheme!r}")
    else:
        token = authorization.strip()

    if not token:
        raise AuthenticationError("empty token")
    return token


class ClaimsMapper:
    """Turns verified claims into a tenant scope.

    Separate from verification because the two answer different questions.
    Verification asks whether the token is genuine; mapping asks which tenant
    the subject is acting for. An identity provider can be trusted for the
    first and not the second — a token proves who someone is, not what they
    may reach.
    """

    __slots__ = ("_tenant_claim", "_resolve_membership")

    def __init__(
        self,
        tenant_claim: str = "tenant",
        resolve_membership: Callable[[str], tuple[str, frozenset[Role]]] | None = None,
    ) -> None:
        self._tenant_claim = tenant_claim
        # Supplied by the runtime to look the subject's membership up in the
        # database. Where a token carries no tenant claim, this is the only
        # authority on which tenant a subject belongs to.
        self._resolve_membership = resolve_membership

    def to_scope(self, claims: VerifiedClaims) -> TenantScope:
        if claims.tenant:
            return TenantScope(
                tenant_id=TenantId(claims.tenant),
                user_id=UserId(claims.subject),
                roles=claims.roles or frozenset({Role.MEMBER}),
            )

        if self._resolve_membership is None:
            raise AuthenticationError(
                f"token carries no {self._tenant_claim!r} claim and no "
                "membership resolver is configured"
            )

        tenant, roles = self._resolve_membership(claims.subject)
        return TenantScope(
            tenant_id=TenantId(tenant),
            user_id=UserId(claims.subject),
            roles=roles or frozenset({Role.MEMBER}),
        )


class IdentityResolver:
    """Produces the scope a request runs under."""

    __slots__ = ("_settings", "_verifier", "_mapper")

    def __init__(
        self,
        settings: Settings,
        verifier: TokenVerifier | None = None,
        mapper: ClaimsMapper | None = None,
    ) -> None:
        if settings.authenticates_requests and verifier is None:
            # A hosted deployment with nothing to verify tokens would have to
            # either reject everything or trust everything.
            raise ValueError("hosted deployment requires a token verifier")
        self._settings = settings
        self._verifier = verifier
        self._mapper = mapper or ClaimsMapper()

    async def resolve(
        self,
        *,
        authorization: str | None = None,
        service_token: str | None = None,
        acting_tenant: str | None = None,
        acting_user: str | None = None,
    ) -> TenantScope:
        if service_token is not None:
            return self._from_service_token(
                service_token, acting_tenant=acting_tenant, acting_user=acting_user
            )

        if not self._settings.authenticates_requests:
            return self._solo_scope(self._settings.auth)

        return await self._from_token(authorization)

    # -- routes ------------------------------------------------------------

    @staticmethod
    def _solo_scope(auth: AuthSettings) -> TenantScope:
        return TenantScope(
            tenant_id=TenantId(auth.solo_tenant_id),
            user_id=UserId(auth.solo_user_id),
            roles=frozenset({Role.OWNER}),
        )

    async def _from_token(self, authorization: str | None) -> TenantScope:
        if not authorization:
            raise AuthenticationError("no Authorization header")

        assert self._verifier is not None  # guaranteed by __init__
        return self._mapper.to_scope(await self._verifier.verify(bare_token(authorization)))

    def _from_service_token(
        self,
        presented: str,
        *,
        acting_tenant: str | None,
        acting_user: str | None,
    ) -> TenantScope:
        configured = self._settings.auth.service_token
        if configured is None:
            # Not "wrong token" — this deployment has no service channel at
            # all, and saying so is less informative to a prober.
            raise AuthenticationError("service token presented but none configured")

        if not hmac.compare_digest(presented, configured.get_secret_value()):
            raise AuthenticationError("service token mismatch")

        if not acting_tenant:
            # The whole point of this channel is acting for a tenant. Guessing
            # one would mean writing another tenant's data on a typo.
            raise AuthenticationError("service call did not name a tenant")

        return TenantScope(
            tenant_id=TenantId(acting_tenant),
            user_id=UserId(acting_user or f"service:{self._settings.service_name}"),
            roles=frozenset({Role.SERVICE}),
        )


class StaticVerifier:
    """A verifier backed by a fixed table. For tests and for solo deployments.

    Named for what it is so that finding it wired into a hosted deployment is
    obvious during review.
    """

    __slots__ = ("_tokens",)

    def __init__(self, tokens: dict[str, VerifiedClaims]) -> None:
        self._tokens = dict(tokens)

    async def verify(self, token: str) -> VerifiedClaims:
        try:
            return self._tokens[token]
        except KeyError:
            raise AuthenticationError("unknown token") from None


def required_verification_parameters(auth: AuthSettings) -> dict[str, object]:
    """The claims a real verifier must check.

    Collected in one place because the reference implementation checked the
    signature and the audience but not the issuer, and the omission was
    invisible: every individual check present was correct.
    """
    return {
        "algorithms": list(auth.algorithms),
        "audience": auth.audience,
        "issuer": auth.issuer,
        "require": ["exp", "iat", "iss", "aud", "sub"],
        "verify_exp": True,
        "verify_aud": True,
        "verify_iss": True,
    }
