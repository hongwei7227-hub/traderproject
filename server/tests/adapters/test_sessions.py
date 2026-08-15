"""Reading the login service's sessions.

The login service is the authority on identity; this platform only reads what
it wrote. The cases worth holding are the ones where the two could disagree: a
key that has expired between services, a hash that exists but is unusable, and
the sliding expiry that keeps a user logged in here while they are working
here rather than only while they are talking to the login service.
"""

from __future__ import annotations

import pytest

from kairos.adapters.identity import RedisSessions, Session, SessionVerifier
from kairos.core.identity import AuthenticationError
from kairos.core.tenancy import Role


class FakeRedis:
    """Enough Redis to exercise the lookup, and no more.

    Values are bytes because that is what a client built without
    `decode_responses` hands back, and getting that wrong turns every username
    into `b'alice'`.
    """

    def __init__(self, hashes: dict[str, dict[str, str]] | None = None) -> None:
        self.hashes = {
            key: {k.encode(): v.encode() for k, v in fields.items()}
            for key, fields in (hashes or {}).items()
        }
        self.expiries: list[tuple[str, int]] = []

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int) -> None:
        self.expiries.append((key, seconds))


def store(**fields: str) -> RedisSessions:
    redis = FakeRedis({"login:token:tok": fields} if fields else {})
    return RedisSessions(redis)


class TestKeyFormat:
    async def test_it_reads_the_key_the_login_service_writes(self) -> None:
        redis = FakeRedis({"login:token:tok": {"id": "7", "username": "alice"}})
        session = await RedisSessions(redis).lookup("tok")
        assert session == Session(user_id="7", username="alice")

    async def test_the_prefix_is_configurable(self) -> None:
        """One setting moves if the login service renames its keys.

        A hard-coded prefix would turn that rename into everyone being logged
        out with no obvious cause.
        """
        redis = FakeRedis({"auth:t:tok": {"id": "7", "username": "alice"}})
        sessions = RedisSessions(redis, key_prefix="auth:t:")
        assert await sessions.lookup("tok") is not None


class TestLookup:
    async def test_an_absent_session_is_none(self) -> None:
        assert await store().lookup("tok") is None

    async def test_bytes_and_text_fields_read_the_same(self) -> None:
        session = await store(id="7", username="alice").lookup("tok")
        assert session is not None
        assert session.username == "alice"

    async def test_a_hash_without_a_user_is_treated_as_absent(self) -> None:
        """A corrupt row is not an outage.

        The caller cannot act on the difference between "expired" and
        "unusable", and raising here would turn one bad row into a 500 for
        whoever holds it.
        """
        assert await store(username="alice").lookup("tok") is None

    async def test_a_missing_username_is_not_fatal(self) -> None:
        """The id is what identifies; the name is for display."""
        session = await store(id="7").lookup("tok")
        assert session is not None
        assert session.user_id == "7"


class TestSlidingExpiry:
    async def test_reading_a_session_extends_it(self) -> None:
        """Otherwise a session lapses while its owner is working here.

        The login service slides the window on its own requests. A user who
        logs in and then spends an hour in this platform never touches it
        again, and would be logged out mid-conversation.
        """
        redis = FakeRedis({"login:token:tok": {"id": "7"}})
        sessions = RedisSessions(redis, ttl_minutes=30)
        await sessions.refresh("tok")
        assert redis.expiries == [("login:token:tok", 1800)]

    async def test_it_can_be_left_to_the_other_side(self) -> None:
        """Two components sliding the same window would fight over it."""
        redis = FakeRedis({"login:token:tok": {"id": "7"}})
        await RedisSessions(redis, refresh_on_read=False).refresh("tok")
        assert redis.expiries == []


class FakeStore:
    def __init__(self, sessions: dict[str, Session]) -> None:
        self.sessions = sessions
        self.refreshed: list[str] = []

    async def lookup(self, token: str) -> Session | None:
        return self.sessions.get(token)

    async def refresh(self, token: str) -> None:
        self.refreshed.append(token)


class TestVerifier:
    async def test_a_live_session_yields_claims(self) -> None:
        verifier = SessionVerifier(
            FakeStore({"tok": Session(user_id="7", username="alice", tenant="acme")})
        )
        claims = await verifier.verify("tok")
        assert claims.subject == "7"
        assert claims.tenant == "acme"
        assert Role.MEMBER in claims.roles

    async def test_an_unknown_token_is_refused(self) -> None:
        with pytest.raises(AuthenticationError):
            await SessionVerifier(FakeStore({})).verify("tok")

    async def test_a_refused_token_does_not_extend_anything(self) -> None:
        store = FakeStore({})
        with pytest.raises(AuthenticationError):
            await SessionVerifier(store).verify("tok")
        assert store.refreshed == []

    async def test_a_served_request_extends_the_session(self) -> None:
        store = FakeStore({"tok": Session(user_id="7", username="alice")})
        await SessionVerifier(store).verify("tok")
        assert store.refreshed == ["tok"]

    async def test_a_session_without_a_tenant_leaves_it_unresolved(self) -> None:
        """The login service answers "who", not "acting for whom".

        Returning None here sends the question to membership resolution, which
        is the only authority on it.
        """
        verifier = SessionVerifier(FakeStore({"tok": Session(user_id="7", username="a")}))
        assert (await verifier.verify("tok")).tenant is None

    async def test_a_single_tenant_deployment_can_name_its_tenant(self) -> None:
        verifier = SessionVerifier(
            FakeStore({"tok": Session(user_id="7", username="a")}),
            default_tenant="acme",
        )
        assert (await verifier.verify("tok")).tenant == "acme"

    async def test_the_session_wins_over_the_default(self) -> None:
        """A default is a fallback, not an override.

        Reversing this would put a user with a real tenant into the wrong one.
        """
        verifier = SessionVerifier(
            FakeStore({"tok": Session(user_id="7", username="a", tenant="real")}),
            default_tenant="fallback",
        )
        assert (await verifier.verify("tok")).tenant == "real"
