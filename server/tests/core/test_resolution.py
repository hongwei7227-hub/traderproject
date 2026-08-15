"""Precedence between the layers that can name a model."""

from __future__ import annotations

import pytest

from kairos.core.catalog import (
    ModelId,
    ModelResolutionChain,
    NoModelAvailable,
    ResolutionRequest,
    Role,
)
from kairos.core.tenancy import TenantId, UserId


def request_for(
    role: str = Role.PRIMARY,
    *,
    explicit: str | None = None,
    tenant: dict[str, str] | None = None,
    workspace: dict[str, str] | None = None,
    baseline: dict[str, str] | None = None,
) -> ResolutionRequest:
    return ResolutionRequest(
        role=role,
        tenant_id=TenantId("acme"),
        user_id=UserId("u-1"),
        explicit=ModelId(explicit) if explicit else None,
        tenant_preferences={k: ModelId(v) for k, v in (tenant or {}).items()},
        workspace_defaults={k: ModelId(v) for k, v in (workspace or {}).items()},
        baseline={k: ModelId(v) for k, v in (baseline or {}).items()},
    )


class TestPrecedence:
    def test_explicit_request_wins(self) -> None:
        choice = ModelResolutionChain().resolve(
            request_for(
                explicit="asked-for",
                tenant={Role.PRIMARY: "tenant-pick"},
                baseline={Role.PRIMARY: "house-default"},
            )
        )
        assert choice.model_id == "asked-for"
        assert choice.decided_by == "explicit-request"

    def test_tenant_preference_beats_workspace_and_baseline(self) -> None:
        choice = ModelResolutionChain().resolve(
            request_for(
                tenant={Role.PRIMARY: "tenant-pick"},
                workspace={Role.PRIMARY: "workspace-pick"},
                baseline={Role.PRIMARY: "house-default"},
            )
        )
        assert choice.model_id == "tenant-pick"

    def test_workspace_beats_baseline(self) -> None:
        choice = ModelResolutionChain().resolve(
            request_for(
                workspace={Role.PRIMARY: "workspace-pick"},
                baseline={Role.PRIMARY: "house-default"},
            )
        )
        assert choice.model_id == "workspace-pick"

    def test_baseline_is_the_last_stop(self) -> None:
        choice = ModelResolutionChain().resolve(
            request_for(baseline={Role.PRIMARY: "house-default"})
        )
        assert choice.model_id == "house-default"
        assert choice.decided_by == "system-baseline"


class TestRoleIsolation:
    def test_an_explicit_request_cannot_redirect_a_secondary_role(self) -> None:
        """Naming a model on the request steers the primary role only.

        Otherwise a caller could point the condensing role at an expensive
        model and move cost onto a role the tenant never chose.
        """
        choice = ModelResolutionChain().resolve(
            request_for(
                role=Role.CONDENSE,
                explicit="expensive-flagship",
                baseline={Role.CONDENSE: "cheap-small"},
            )
        )
        assert choice.model_id == "cheap-small"

    def test_roles_resolve_independently(self) -> None:
        chain = ModelResolutionChain()
        prefs = {Role.PRIMARY: "big", Role.CONDENSE: "small"}

        assert chain.resolve(request_for(Role.PRIMARY, tenant=prefs)).model_id == "big"
        assert chain.resolve(request_for(Role.CONDENSE, tenant=prefs)).model_id == "small"

    def test_a_role_configured_nowhere_falls_through(self) -> None:
        # The tenant set a primary model but never chose an extraction model;
        # the extraction role must not inherit it by accident.
        with pytest.raises(NoModelAvailable) as caught:
            ModelResolutionChain().resolve(
                request_for(Role.EXTRACT, tenant={Role.PRIMARY: "big"})
            )
        assert caught.value.role == Role.EXTRACT

    def test_delegate_roles_are_namespaced(self) -> None:
        key = Role.delegate("researcher")
        assert key == "delegate:researcher"
        choice = ModelResolutionChain().resolve(request_for(key, tenant={key: "sub-model"}))
        assert choice.model_id == "sub-model"


class TestExhaustion:
    def test_no_answer_anywhere_raises(self) -> None:
        with pytest.raises(NoModelAvailable):
            ModelResolutionChain().resolve(request_for())

    def test_the_error_names_every_level_consulted(self) -> None:
        with pytest.raises(NoModelAvailable) as caught:
            ModelResolutionChain().resolve(request_for())
        assert caught.value.tried == (
            "explicit-request",
            "tenant-preference",
            "workspace-default",
            "system-baseline",
        )

    def test_optional_roles_can_ask_without_raising(self) -> None:
        assert ModelResolutionChain().resolve_or_none(request_for(Role.CONDENSE)) is None


class TestChainComposition:
    def test_an_empty_chain_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            ModelResolutionChain([])

    def test_a_custom_chain_can_drop_a_level(self) -> None:
        """A deployment that does not do per-workspace settings can omit it.

        The point of the chain being a sequence is that this is a composition
        change, not an edit inside a resolution function.
        """
        from kairos.core.catalog import SystemBaselineResolver, TenantPreferenceResolver

        chain = ModelResolutionChain([TenantPreferenceResolver(), SystemBaselineResolver()])
        choice = chain.resolve(
            request_for(
                workspace={Role.PRIMARY: "workspace-pick"},
                baseline={Role.PRIMARY: "house-default"},
            )
        )
        assert choice.model_id == "house-default"

    def test_provenance_is_reported(self) -> None:
        # Answering "why did my request go there?" should not require reading
        # the chain by hand.
        choice = ModelResolutionChain().resolve(
            request_for(tenant={Role.PRIMARY: "tenant-pick"})
        )
        assert "tenant-preference" in str(choice)
