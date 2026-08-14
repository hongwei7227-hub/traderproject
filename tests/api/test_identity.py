"""Who a request runs as, and what it takes to convince us."""

from __future__ import annotations

import pytest

from kairos.api.http.identity import (
    AuthenticationError,
    ClaimsMapper,
    IdentityResolver,
    StaticVerifier,
    VerifiedClaims,
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
    defaults: dict[str, object] = {
        "jwks_url": "https://issuer.example/jwks",
        "issuer": "https://issuer.example",
    }
    return Settings(deployment=Deployment.HOSTED, auth=AuthSettings(**(defaults | auth)))  # type: ignore[arg-type]


def verifier(**tokens: VerifiedClaims) -> StaticVerifier:
    return StaticVerifier(dict(tokens))


class TestSoloMode:
    def test_requests_run_as_the_built_in_tenant(self) -> None:
        scope = IdentityResolver(solo_settings()).resolve()
        assert scope.tenant_id == "solo"
        assert scope.user_id == "operator"
        assert scope.has_role(Role.OWNER)

    def test_the_built_in_identity_is_configurable(self) -> None:
        settings = solo_settings(solo_tenant_id="lab", solo_user_id="me")
        scope = IdentityResolver(settings).resolve()
        assert (scope.tenant_id, scope.user_id) == ("lab", "me")

    def test_no_token_is_required(self) -> None:
        # Nobody else can reach a solo instance, so demanding a token would be
        # ceremony rather than security.
        assert IdentityResolver(solo_settings()).resolve(authorization=None)


class TestHostedMode:
    def test_a_verifier_is_mandatory(self) -> None:
        """Otherwise the deployment must reject everything or trust everything."""
        with pytest.raises(ValueError, match="token verifier"):
            IdentityResolver(hosted_settings())

    def test_a_valid_token_yields_its_tenant(self) -> None:
        resolver = IdentityResolver(
            hosted_settings(),
            verifier(good=VerifiedClaims(subject="alice", tenant="acme")),
        )
        scope = resolver.resolve(authorization="Bearer good")
        assert (scope.tenant_id, scope.user_id) == ("acme", "alice")

    def test_a_missing_header_is_refused(self) -> None:
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError):
            resolver.resolve(authorization=None)

    @pytest.mark.parametrize(
        "header", ["good", "Basic good", "Bearer", "Bearer ", ""]
    )
    def test_malformed_headers_are_refused(self, header: str) -> None:
        resolver = IdentityResolver(
            hosted_settings(), verifier(good=VerifiedClaims(subject="alice", tenant="acme"))
        )
        with pytest.raises(AuthenticationError):
            resolver.resolve(authorization=header)

    def test_an_unknown_token_is_refused(self) -> None:
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError):
            resolver.resolve(authorization="Bearer forged")

    def test_the_reason_is_not_disclosed(self) -> None:
        """The caller learns that it failed, not why.

        Distinguishing expired from malformed from wrong-issuer helps a prober
        more than it helps a legitimate client, which already has a working
        token or none at all.
        """
        resolver = IdentityResolver(hosted_settings(), verifier())
        with pytest.raises(AuthenticationError) as caught:
            resolver.resolve(authorization="Bearer forged")
        assert str(caught.value) == "Not authenticated"
        assert caught.value.reason  # kept for the log


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
    def test_it_is_refused_when_none_is_configured(self) -> None:
        resolver = IdentityResolver(solo_settings())
        with pytest.raises(AuthenticationError):
            resolver.resolve(service_token="anything", acting_tenant="acme")

    def test_a_matching_token_may_act_for_a_named_tenant(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = resolver.resolve(service_token="s3cret", acting_tenant="acme")
        assert scope.tenant_id == "acme"
        assert scope.is_service()

    def test_a_wrong_token_is_refused(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        with pytest.raises(AuthenticationError):
            resolver.resolve(service_token="wrong", acting_tenant="acme")

    def test_the_tenant_must_be_named(self) -> None:
        """Guessing would mean writing another tenant's data on a typo."""
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        with pytest.raises(AuthenticationError, match="Not authenticated"):
            resolver.resolve(service_token="s3cret", acting_tenant=None)

    def test_the_acting_user_is_recorded(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = resolver.resolve(
            service_token="s3cret", acting_tenant="acme", acting_user="importer"
        )
        assert scope.user_id == "importer"

    def test_an_unnamed_service_still_identifies_itself(self) -> None:
        resolver = IdentityResolver(solo_settings(service_token="s3cret"))
        scope = resolver.resolve(service_token="s3cret", acting_tenant="acme")
        assert scope.user_id.startswith("service:")


class TestVerificationParameters:
    def test_the_issuer_is_among_the_required_claims(self) -> None:
        """The check the reference implementation omitted.

        Signature and audience were verified; the issuer was not. Every check
        that was present was correct, which is exactly why the gap survived —
        so the full set is asserted here rather than left to a code reading.
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
    def test_hosted_mode_requires_an_issuer(self) -> None:
        """Failing at startup beats silently serving everyone as one tenant."""
        with pytest.raises(ValueError, match="issuer"):
            Settings(
                deployment=Deployment.HOSTED,
                auth=AuthSettings(jwks_url="https://issuer.example/jwks", issuer=None),
            )

    def test_hosted_mode_requires_a_jwks_url(self) -> None:
        with pytest.raises(ValueError, match="jwks_url"):
            Settings(
                deployment=Deployment.HOSTED,
                auth=AuthSettings(jwks_url=None, issuer="https://issuer.example"),
            )

    def test_solo_mode_needs_neither(self) -> None:
        assert Settings(deployment=Deployment.SOLO).authenticates_requests is False
