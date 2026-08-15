"""The catalogue must reject inconsistency at construction, not at call time."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kairos.core.catalog import (
    Access,
    Capability,
    Endpoint,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    TokenBudget,
    Wire,
)
from kairos.core.catalog.registry import (
    Catalog,
    CatalogError,
    UnknownModel,
    UnknownProvider,
)

BASELINE = Capability.baseline()


def provider(
    pid: str,
    *,
    family: str | None = None,
    wire: Wire = Wire.OPENAI_CHAT,
    access: Access = Access.API_KEY,
    byok: bool = False,
    env: str | None = "KEY",
) -> ProviderDescriptor:
    return ProviderDescriptor(
        id=ProviderId(pid),
        display_name=pid.title(),
        endpoint=Endpoint(wire=wire, credential_env=env, access=access),
        family=ProviderId(family) if family else None,
        byok_allowed=byok,
    )


def model(
    mid: str,
    *,
    pid: str = "vendor",
    caps: Capability = BASELINE,
    selectable: bool = True,
    context: int = 200_000,
    max_output: int = 8_000,
) -> ModelDescriptor:
    return ModelDescriptor(
        id=ModelId(mid),
        remote_id=mid,
        provider=ProviderId(pid),
        budget=TokenBudget(context=context, max_output=max_output),
        capabilities=caps,
        selectable=selectable,
    )


class TestConstructionRejectsInconsistency:
    def test_model_naming_an_undefined_provider(self) -> None:
        with pytest.raises(CatalogError, match="not defined"):
            Catalog(providers=[provider("vendor")], models=[model("m", pid="ghost")])

    def test_family_pointing_at_an_undefined_provider(self) -> None:
        with pytest.raises(CatalogError, match="not defined"):
            Catalog(providers=[provider("vendor-cn", family="ghost")], models=[])

    def test_provider_that_is_its_own_family(self) -> None:
        # Would make credential-chain walking loop.
        with pytest.raises(CatalogError, match="its own family"):
            Catalog(providers=[provider("vendor", family="vendor")], models=[])

    def test_duplicate_model_ids(self) -> None:
        with pytest.raises(CatalogError, match="duplicate model"):
            Catalog(providers=[provider("vendor")], models=[model("m"), model("m")])

    def test_duplicate_provider_ids(self) -> None:
        with pytest.raises(CatalogError, match="duplicate provider"):
            Catalog(providers=[provider("vendor"), provider("vendor")], models=[])


class TestDescriptorValidation:
    def test_selectable_model_must_meet_the_baseline(self) -> None:
        # A model that cannot call tools cannot drive an agent turn. Catching
        # this at declaration beats discovering it mid-conversation.
        with pytest.raises(ValidationError, match="lacks"):
            model("text-only", caps=Capability.TEXT | Capability.STREAMING)

    def test_non_selectable_models_are_exempt(self) -> None:
        assert model("embed", caps=Capability.TEXT, selectable=False)

    def test_authenticating_endpoint_needs_a_credential_source(self) -> None:
        with pytest.raises(ValidationError, match="could never authenticate"):
            Endpoint(wire=Wire.OPENAI_CHAT, access=Access.API_KEY, credential_env=None)

    def test_local_endpoints_need_no_credentials(self) -> None:
        assert Endpoint(wire=Wire.OPENAI_CHAT, access=Access.LOCAL, credential_env=None)


class TestCompactionThreshold:
    def test_threshold_scales_with_the_context_window(self) -> None:
        """The whole point: falling back moves the threshold with it.

        The reference implementation pinned compaction to an absolute token
        count, so a million-token model compacted at 12% of its window while a
        fallback with a fifth the context used the same number.
        """
        large = TokenBudget(context=1_000_000, max_output=32_000)
        small = TokenBudget(context=200_000, max_output=32_000)
        assert large.compaction_threshold() > small.compaction_threshold()

    def test_threshold_leaves_room_for_the_reply(self) -> None:
        budget = TokenBudget(context=200_000, max_output=32_000)
        assert budget.compaction_threshold() < budget.context - budget.max_output


class TestLookup:
    def test_unknown_model_lists_what_exists(self) -> None:
        catalog = Catalog([provider("vendor")], [model("real")])
        with pytest.raises(UnknownModel, match="real"):
            catalog.model("imaginary")

    def test_unknown_provider(self) -> None:
        with pytest.raises(UnknownProvider):
            Catalog([], []).provider("ghost")

    def test_provider_for_model(self) -> None:
        catalog = Catalog([provider("vendor")], [model("m", pid="vendor")])
        assert catalog.provider_for("m").id == "vendor"

    def test_membership_and_length(self) -> None:
        catalog = Catalog([provider("vendor")], [model("a"), model("b")])
        assert len(catalog) == 2
        assert "a" in catalog
        assert "z" not in catalog


class TestCapabilityQueries:
    def test_capable_of_filters_by_capability(self) -> None:
        catalog = Catalog(
            [provider("vendor")],
            [model("plain"), model("seeing", caps=BASELINE | Capability.VISION)],
        )
        seeing = catalog.capable_of(Capability.VISION)
        assert [m.id for m in seeing] == ["seeing"]

    def test_non_selectable_models_are_never_offered(self) -> None:
        catalog = Catalog(
            [provider("vendor")],
            [model("chat"), model("embed", caps=Capability.TEXT, selectable=False)],
        )
        assert [m.id for m in catalog.selectable()] == ["chat"]
        assert all(m.id != "embed" for m in catalog.capable_of(Capability.TEXT))


class TestCredentialFamilies:
    def test_the_endpoint_itself_comes_first(self) -> None:
        """Nearest-first ordering, so a specific key beats an inherited one.

        Siblings can differ in wire protocol and credential variable; treating
        them as interchangeable produces 401s that read as auth failures rather
        than as missing configuration.
        """
        catalog = Catalog(
            [
                provider("vendor"),
                provider("vendor-intl", family="vendor"),
                provider("vendor-coding", family="vendor", wire=Wire.ANTHROPIC_MESSAGES),
            ],
            [],
        )
        assert catalog.family_of("vendor-coding")[0] == "vendor-coding"
        assert set(catalog.family_of("vendor-coding")) == {
            "vendor",
            "vendor-intl",
            "vendor-coding",
        }

    def test_a_standalone_provider_is_its_own_family(self) -> None:
        catalog = Catalog([provider("solo")], [])
        assert catalog.family_of("solo") == ("solo",)

    def test_siblings_may_speak_different_protocols(self) -> None:
        # The reason family membership cannot imply an interchangeable client.
        catalog = Catalog(
            [
                provider("vendor"),
                provider("vendor-coding", family="vendor", wire=Wire.ANTHROPIC_MESSAGES),
            ],
            [],
        )
        assert catalog.provider("vendor").endpoint.wire is Wire.OPENAI_CHAT
        assert catalog.provider("vendor-coding").endpoint.wire is Wire.ANTHROPIC_MESSAGES


class TestByokEligibility:
    def test_defaults_closed(self) -> None:
        # The reference implementation defaulted this open, which advertised
        # every endpoint as a place to paste a key.
        catalog = Catalog([provider("vendor")], [])
        assert catalog.byok_providers() == ()

    def test_opt_in_is_honoured(self) -> None:
        catalog = Catalog([provider("vendor", byok=True)], [])
        assert catalog.byok_providers() == ("vendor",)

    def test_oauth_endpoints_are_excluded_even_when_flagged(self) -> None:
        # There is no API key to paste for an OAuth endpoint.
        catalog = Catalog(
            [provider("vendor-oauth", access=Access.OAUTH, byok=True, env=None)], []
        )
        assert catalog.byok_providers() == ()

    def test_local_endpoints_are_excluded_even_when_flagged(self) -> None:
        catalog = Catalog(
            [provider("local-rig", access=Access.LOCAL, byok=True, env=None)], []
        )
        assert catalog.byok_providers() == ()
