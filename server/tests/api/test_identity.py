"""Who a request runs as, and what it takes to convince us."""

from __future__ import annotations

import pytest

from kairos.api.http.identity import (
    AuthenticationError,
    ClaimsMapper,
    IdentityResolver,
    StaticVerifier,
    VerifiedClaims,
    bare_token,
    required_verification_parameters,
)
from kairos.core.tenancy import Role
from kairos.runtime.settings import (
    AuthSettings,
    Deployment,
    Settings,
)


def solo_settings(**auth: object) -> Settings:
    return Settings(deployment=Deployment.SOLO, auth=AuthSettings(**auth))  # type: ignore[arg-type]


def hosted_settings(**auth: object) -> Settings:
    """A hosted deployment on the external-issuer provider.

    The JWT settings are supplied here because that is the provider these tests
    exercise; a deployment on the login service configures neither.
    """
    defaults: dict[str, object] = {
        "provider": "jwt",
        "jwks_url": "https://issuer.example/jwks",
        "issuer": "https://issuer.example",
    }
    return Settings(deployment=Deployment.HOSTED, auth=AuthSettings(**(defaults | auth)))  # type: ignore[arg-type]


def session_settings(**auth: object) -> Settings:
    return Settings(deployment=Deployment.HOSTED, auth=AuthSettings(**auth))  # type: ignore[arg-type]


def verifier(**tokens: VerifiedClaims) -> StaticVerifier:
    return StaticVerifier(dict(tokens))


class TestSoloMode:
    async def test_requests_run_as_the_built_in_tenant(self) -> None:
        scope = await IdentityResolver(solo_settings()).resolve()
        assert scope.tenant_id == "solo"
        assert scope.user_id == "operator"
        assert scope.has_role(Role.OWNER)

    async def test_the_built_in_identity_is_configurable(self) -> None:
        settings = solo_settings(solo_tenant_id="lab", solo_user_id="me")
        scope = await IdentityResolver(settings).resolve()
        assert (scope.tenant_id, scope.user_id) == ("lab", "me")

    async def test_no_token_is_required(self) -> None:
        # Nobody else can reach a solo instance, so demanding a token would be
        # ceremony rather than security.
        assert await IdentityResolver(solo_settings()).resolve(authorization=None)


class TestHostedMode:
    def test_a_verifier_is_mandatory(self) -> None:
        """Otherwise the deployment must reject everything or trust everything."""
        with pytest.raises(ValueError, match="token verifier"):
            IdentityResolver(hosted_settings())

    async def test_a_valid_token_yields_its_tenant(self) -> None:
        resolver = IdentityResolver(
            hosted_settings(),
            verifier(good=VerifiedClaims(subject="alice", tenant="acme")),
        )
        scope = await resolver.resolve(authorization="Bearer good")
        assert (scope.tenant_id, scope.user_id) == ("acme", "alice")

    async def test_a_missing_header_is_refused(self) -> None:
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization=None)

    @pytest.mark.parametrize("header", ["Basic good", "Digest x", "Bearer ", ""])
    async def test_malformed_headers_are_refused(self, header: str) -> None:
        resolver = IdentityResolver(
            hosted_settings(), verifier(good=VerifiedClaims(subject="alice", tenant="acme"))
        )
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization=header)

    async def test_an_unknown_token_is_refused(self) -> None:
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError):
            await resolver.resolve(authorization="Bearer forged")

    async def test_the_reason_is_not_disclosed(self) -> None:
        """The caller learns that it failed, not why.

        Distinguishing expired from malformed from wrong-issuer helps a prober
        more than it helps a legitimate client, which already has a working
        token or none at all.
        """
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError) as caught:
            await resolver.resolve(authorization="Bearer forged")
        assert str(caught.value) == "Not authenticated"
        assert caught.value.reason  # kept for the log


class TestTokenExtraction:
    """Both header shapes reach this platform.

    A browser session sends `Bearer <token>`; the login service's own clients
    send the token alone, because its interceptor reads the header raw.
    Accepting only one would mean a token that works against half the system.
    """

    def test_a_bearer_token_has_its_scheme_stripped(self) -> None:
        assert bare_token("Bearer abc123") == "abc123"

    def test_a_bare_token_is_taken_as_is(self) -> None:
        assert bare_token("abc123") == "abc123"

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert bare_token("Bearer  abc123 ") == "abc123"

    def test_another_scheme_is_refused_rather_than_treated_as_a_token(self) -> None:
        """`Basic <base64>` is an encoded password, not an opaque token.

        Looking one up as a session would send credentials into a cache key.
        """
        with pytest.raises(AuthenticationError):
            bare_token("Basic dXNlcjpwYXNz")

    @pytest.mark.parametrize("header", ["Bearer ", "Bearer   ", "   "])
    def test_an_empty_token_is_refused(self, header: str) -> None:
        with pytest.raises(AuthenticationError):
            bare_token(header)


class TestSessionProvider:
    """A hosted deployment backed by the login service."""

    async def test_a_bare_token_from_the_login_service_is_accepted(self) -> None:
        resolver = IdentityResolver(
            session_settings(),
            verifier(a1b2c3=VerifiedClaims(subject="7", tenant="acme")),
        )
        scope = await resolver.resolve(authorization="a1b2c3")
        assert (scope.tenant_id, scope.user_id) == ("acme", "7")

    def test_it_does_not_require_an_external_issuer(self) -> None:
        """The login service issues the tokens; there is no JWKS to fetch."""
        settings = session_settings()
        assert settings.auth.provider == "session"
        assert settings.auth.jwks_url is None


class TestClaimsMapping:
    def test_a_token_without_a_tenant_needs_a_membership_lookup(self) -> None:
        mapper = ClaimsMapper()
        with pytest.raises(AuthenticationError):
            mapper.to_scope(VerifiedClaims(subject="alice"))

    def test_membership_resolution_supplies_the_tenant(self) -> None:
        """An identity provider proves who someone is, not what they may reach.

        Where the token carries no tenant, membership in the database is the
        only authority on which tenant a subject acts for.
        """
        mapper = ClaimsMapper(
            resolve_membership=lambda subject: ("acme", frozenset({Role.OWNER}))
        )
        scope = mapper.to_scope(VerifiedClaims(subject="alice"))
        assert scope.tenant_id == "acme"
        assert scope.has_role(Role.OWNER)

    def test_a_subject_defaults_to_member(self) -> None:
        mapper = ClaimsMapper()
        scope = mapper.to_scope(VerifiedClaims(subject="alice", tenant="acme"))
        assert scope.has_role(Role.MEMBER)


class TestServiceToken:
    async def test_it_is_refused_when_none_is_configured(self) -> None:
        resolver = IdentityResolver(solo_settings())
        with pytest.raises(AuthenticationError):
            await resolver.resolve(service_token="anything", acting_tenant="acme")

    async def test_a_matching_token_may_act_for_a_named_tenant(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = await resolver.resolve(service_token="s3cret", acting_tenant="acme")
        assert scope.tenant_id == "acme"
        assert scope.is_service()

    async def test_a_wrong_token_is_refused(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        with pytest.raises(AuthenticationError):
            await resolver.resolve(service_token="wrong", acting_tenant="acme")

    async def test_the_tenant_must_be_named(self) -> None:
        """Guessing would mean writing another tenant's data on a typo."""
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        with pytest.raises(AuthenticationError, match="Not authenticated"):
            await resolver.resolve(service_token="s3cret", acting_tenant=None)

    async def test_the_acting_user_is_recorded(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = await resolver.resolve(
            service_token="s3cret", acting_tenant="acme", acting_user="importer"
        )
        assert scope.user_id == "importer"

    async def test_an_unnamed_service_still_identifies_itself(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = await resolver.resolve(service_token="s3cret", acting_tenant="acme")
        assert scope.user_id.startswith("service:")


class TestVerificationParameters:
    def test_the_issuer_is_among_the_required_claims(self) -> None:
        """Signature and audience alone are not enough.

        A token minted by any project of the same identity provider, using the
        same audience string, passes both. The issuer is what distinguishes
        them, so the full required set is asserted here rather than left to a
        code reading.
        """
        params = required_verification_parameters(
            AuthSettings(issuer="https://issuer.example")
        )
        assert params["issuer"] == "https://issuer.example"
        assert params["verify_iss"] is True
        assert "iss" in params["require"]  # type: ignore[operator]

    def test_expiry_and_subject_are_required(self) -> None:
        params = required_verification_parameters(AuthSettings())
        assert {"exp", "sub", "aud"} <= set(params["require"])  # type: ignore[arg-type]
        assert params["verify_exp"] is True


class TestHostedConfiguration:
    def test_the_jwt_provider_requires_an_issuer(self) -> None:
        """Failing at startup beats silently serving everyone as one tenant."""
        with pytest.raises(ValueError, match="issuer"):
            Settings(
                deployment=Deployment.HOSTED,
                auth=AuthSettings(
                    provider="jwt", jwks_url="https://issuer.example/jwks", issuer=None
                ),
            )

    def test_the_jwt_provider_requires_a_jwks_url(self) -> None:
        with pytest.raises(ValueError, match="jwks_url"):
            Settings(
                deployment=Deployment.HOSTED,
                auth=AuthSettings(
                    provider="jwt", jwks_url=None, issuer="https://issuer.example"
                ),
            )

    def test_the_session_provider_requires_neither(self) -> None:
        """It reads sessions the login service wrote; there is no issuer to name."""
        settings = Settings(deployment=Deployment.HOSTED, auth=AuthSettings())
        assert settings.authenticates_requests is True

    def test_solo_mode_needs_nothing(self) -> None:
        assert Settings(deployment=Deployment.SOLO).authenticates_requests is False
