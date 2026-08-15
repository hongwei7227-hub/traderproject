"""Who is calling.

The port lives here rather than beside the HTTP layer because more than the
HTTP layer needs it: an adapter implements verification, the delivery layer
consumes it, and neither should have to import the other to agree on what a
verified caller looks like.
"""

from kairos.core.identity.claims import (
    AuthenticationError,
    TokenVerifier,
    VerifiedClaims,
)

__all__ = ["AuthenticationError", "TokenVerifier", "VerifiedClaims"]
