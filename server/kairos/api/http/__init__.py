"""HTTP entry points and the middleware that scopes them."""

from kairos.api.http.identity import (
    AuthenticationError,
    ClaimsMapper,
    IdentityResolver,
    StaticVerifier,
    TokenVerifier,
    VerifiedClaims,
)
from kairos.api.http.middleware import (
    DEFAULT_PUBLIC_PATHS,
    TenantScopeMiddleware,
)

__all__ = [
    "DEFAULT_PUBLIC_PATHS",
    "AuthenticationError",
    "ClaimsMapper",
    "IdentityResolver",
    "StaticVerifier",
    "TenantScopeMiddleware",
    "TokenVerifier",
    "VerifiedClaims",
]
