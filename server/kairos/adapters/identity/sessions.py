"""Reading the login service's sessions.

The login service owns identity. It checks the password, mints an opaque token
and writes the user behind it into Redis under `login:token:<token>`, sliding
the expiry each time the token is used. This platform accepts the same token by
reading the same key.

Reading the key rather than calling the service is deliberate. The alternative
is an HTTP round trip to a service that would then do exactly this lookup, on
every single request — including every frame of a stream. What that buys is the
freedom for the login service to change its storage without telling anyone,
which is why the key format is configuration here rather than a constant: when
it does change, one setting moves and the failure is a clean "not logged in"
rather than a subtle mismatch.

The expiry is refreshed on read, matching the service's own interceptor. A
session that only slid when the user happened to hit the login service would
expire while they were mid-conversation here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kairos.core.identity import AuthenticationError, VerifiedClaims
from kairos.core.tenancy import Role


@dataclass(frozen=True, slots=True)
class Session:
    """A logged-in user, as the login service recorded them.

    `tenant` is optional because the login service does not model tenancy — it
    answers "who is this", and this platform answers "acting for whom". Where
    it is absent the claims mapper consults membership, which is the only
    authority on that question.
    """

    user_id: str
    username: str
    tenant: str | None = None


class SessionStore(Protocol):
    """Where sessions live.

    A protocol so the verifier can be tested against a dictionary. The
    alternative — a test Redis — would make the interesting cases (expired,
    malformed, absent) awkward to set up and slow to run.
    """

    async def lookup(self, token: str) -> Session | None: ...

    async def refresh(self, token: str) -> None: ...


class RedisSessions:
    """The login service's session table, read directly.

    Field names are the login service's: it stores a `UserDTO` as a Redis hash
    with `id` and `username`. They are mapped here rather than assumed
    elsewhere, so a rename on that side is a change to this file alone.
    """

    __slots__ = ("_redis", "_prefix", "_ttl_seconds", "_refresh")

    def __init__(
        self,
        redis: object,
        *,
        key_prefix: str = "login:token:",
        ttl_minutes: int = 30,
        refresh_on_read: bool = True,
    ) -> None:
        # Typed as `object` because the core forbids a redis import and this
        # module is reachable from it through the verifier protocol. The only
        # methods used are `hgetall` and `expire`.
        self._redis = redis
        self._prefix = key_prefix
        self._ttl_seconds = ttl_minutes * 60
        self._refresh = refresh_on_read

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    async def lookup(self, token: str) -> Session | None:
        raw = await self._redis.hgetall(self._key(token))  # type: ignore[attr-defined]
        if not raw:
            return None

        fields = {_text(k): _text(v) for k, v in raw.items()}
        user_id = fields.get("id")
        if not user_id:
            # A hash that exists but carries no user is corrupt, not expired.
            # Treated as "not logged in" rather than raised: the caller cannot
            # act on the difference, and a 500 here would turn a bad row into
            # an outage.
            return None

        return Session(
            user_id=user_id,
            username=fields.get("username", ""),
            tenant=fields.get("tenant") or None,
        )

    async def refresh(self, token: str) -> None:
        if self._refresh:
            await self._redis.expire(self._key(token), self._ttl_seconds)  # type: ignore[attr-defined]


def _text(value: object) -> str:
    """Redis hands back bytes or str depending on how the client was built."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class SessionVerifier:
    """Turns a login-service token into claims.

    Refreshes before answering rather than after. A request that is about to be
    served should not be the one that lets the session lapse, and the ordering
    only matters at the boundary — which is exactly where a user notices.
    """

    __slots__ = ("_sessions", "_default_tenant")

    def __init__(self, sessions: SessionStore, *, default_tenant: str | None = None) -> None:
        self._sessions = sessions
        # For deployments where every login belongs to the same tenant. Left
        # unset in a real multi-tenant deployment, so that a missing tenant
        # goes to membership resolution instead of silently landing everyone
        # in one account.
        self._default_tenant = default_tenant

    async def verify(self, token: str) -> VerifiedClaims:
        session = await self._sessions.lookup(token)
        if session is None:
            raise AuthenticationError("no session for token")

        await self._sessions.refresh(token)

        return VerifiedClaims(
            subject=session.user_id,
            tenant=session.tenant or self._default_tenant,
            roles=frozenset({Role.MEMBER}),
        )
