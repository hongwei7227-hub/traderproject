"""Reading and changing what a tenant has configured.

Three things a tenant can see about its own account: which models it may reach,
which one currently serves each role, and what it has spent. The first two are
the visible face of the resolution chain — without them a tenant can configure a
preference but cannot confirm it took, and "why did my request go somewhere
else?" has no answer short of reading logs.

Validation happens here rather than at request time. A model that cannot fill a
role is refused when it is chosen, which turns a failed turn into a rejected
form submission.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from kairos.core.catalog.descriptors import Capability, ModelDescriptor
from kairos.core.catalog.registry import Catalog
from kairos.core.catalog.resolution import (
    ModelResolutionChain,
    ResolutionRequest,
    Role,
)
from kairos.core.tenancy.context import current_scope

router = APIRouter(tags=["configuration"])


class Selection(Protocol):
    """What this module needs from the composition root.

    Narrower than the container it is satisfied by, so that a test can supply a
    catalogue and a chain without standing up a process.
    """

    catalog: Catalog
    resolution: ModelResolutionChain

    def baseline(self) -> dict[str, str]: ...


def get_selection() -> Selection:
    """Supplied by the composition root at wiring time."""
    raise NotImplementedError("selection dependency is not wired")


def get_repositories():  # type: ignore[no-untyped-def]
    raise NotImplementedError("repository dependency is not wired")


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class ModelView(BaseModel):
    """One model, as a tenant sees it.

    Deliberately does not carry `remote_id`, the base URL or the credential
    variable. Those describe how the platform reaches the provider, which is
    not a tenant's business and is a small map of the deployment.
    """

    id: str
    provider: str
    provider_name: str
    wire: str
    context: int
    max_output: int
    capabilities: list[str]
    eligible_roles: list[str] = Field(
        description="Roles this model has the capabilities to fill.",
    )


class ModelList(BaseModel):
    models: list[ModelView]


class RoleAssignment(BaseModel):
    """Which model serves a role, and what decided that."""

    role: str
    model_id: str
    decided_by: str = Field(
        description=(
            "The precedence level that answered: explicit-request, "
            "tenant-preference, workspace-default or system-baseline."
        ),
    )
    overridden: bool = Field(
        description="Whether this tenant has set a preference for the role.",
    )
    requires: list[str] = Field(
        description="Capabilities a model must have to fill this role.",
    )


class PreferenceList(BaseModel):
    roles: list[RoleAssignment]


class PreferenceUpdate(BaseModel):
    model_id: Annotated[str, Field(min_length=1, max_length=128)]


class UsageView(BaseModel):
    """Tokens this tenant has consumed.

    Input and output are reported separately because they are priced
    separately, and a single total hides the difference between a tenant that
    reads a great deal and one that writes a great deal.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def capability_names(capabilities: Capability) -> list[str]:
    """Flag members as stable lowercase strings.

    The wire carries names rather than the underlying bits: the numeric value
    of a flag shifts whenever a member is inserted, and a client that stored
    one would silently start reading a different capability.
    """
    return sorted(member.name.lower() for member in capabilities if member.name)


def render_model(model: ModelDescriptor, catalog: Catalog) -> ModelView:
    provider = catalog.provider(model.provider)
    return ModelView(
        id=str(model.id),
        provider=str(provider.id),
        provider_name=provider.display_name,
        wire=str(provider.endpoint.wire),
        context=model.budget.context,
        max_output=model.budget.max_output,
        capabilities=capability_names(model.capabilities),
        eligible_roles=[
            str(role) for role in Role if model.supports(role.requires())
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/models", response_model=ModelList)
async def list_models(
    selection: Selection = Depends(get_selection),
) -> ModelList:
    """Models this tenant may choose from.

    Only selectable entries. Non-selectable models exist — a summarizer wired
    into the pipeline, for instance — but offering them as choices invites a
    tenant to pick one for a role it cannot fill.
    """
    return ModelList(
        models=[
            render_model(model, selection.catalog)
            for model in sorted(selection.catalog.selectable(), key=lambda m: m.id)
        ]
    )


@router.get("/preferences", response_model=PreferenceList)
async def list_preferences(
    selection: Selection = Depends(get_selection),
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> PreferenceList:
    """What currently serves each role, and why.

    Resolved rather than read back. Returning the stored preferences alone
    would leave every unset role blank, when in fact each one resolves to
    something — and it is the something that a tenant needs to see.
    """
    scope = current_scope()
    preferences = await repositories.preferences.as_mapping()
    baseline = selection.baseline()

    assignments: list[RoleAssignment] = []
    for role in Role:
        choice = selection.resolution.resolve_or_none(
            ResolutionRequest(
                role=str(role),
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                tenant_preferences=preferences,  # type: ignore[arg-type]
                baseline=baseline,  # type: ignore[arg-type]
            )
        )
        if choice is None:
            # A deployment whose catalogue offers nothing for this role. Shown
            # as an unfilled role rather than omitted, because a role that
            # silently disappears from the list reads as one that does not
            # exist.
            continue
        assignments.append(
            RoleAssignment(
                role=str(role),
                model_id=str(choice.model_id),
                decided_by=choice.decided_by,
                overridden=str(role) in preferences,
                requires=capability_names(role.requires()),
            )
        )
    return PreferenceList(roles=assignments)


@router.put("/preferences/{role}", response_model=RoleAssignment)
async def set_preference(
    role: str,
    update: PreferenceUpdate,
    selection: Selection = Depends(get_selection),
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> RoleAssignment:
    """Point a role at a model.

    Three refusals, in order of how confusing they would be if deferred: an
    unknown role, an unknown or unselectable model, and a model that lacks
    what the role needs. The last is the one worth catching here — it would
    otherwise surface as a turn that fails partway through on a capability the
    caller could not have known was missing.
    """
    resolved_role = _require_role(role)
    catalog = selection.catalog

    if not catalog.has_model(update.model_id):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"No model named {update.model_id!r}",
        )

    model = catalog.model(update.model_id)
    if not model.selectable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Model {update.model_id!r} is not selectable",
        )

    required = resolved_role.requires()
    if not model.supports(required):
        missing = capability_names(required & ~model.capabilities)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Model {update.model_id!r} cannot fill role {role!r}; "
            f"it lacks {', '.join(missing)}",
        )

    await repositories.preferences.set_role(str(resolved_role), update.model_id)

    return RoleAssignment(
        role=str(resolved_role),
        model_id=update.model_id,
        decided_by="tenant-preference",
        overridden=True,
        requires=capability_names(required),
    )


@router.delete("/preferences/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_preference(
    role: str,
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> Response:
    """Drop a tenant override, letting the role fall back down the chain.

    Idempotent: clearing a role that was never set is not an error, because a
    client that has just cleared it has no way to tell the difference.
    """
    await repositories.preferences.clear_role(str(_require_role(role)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/usage", response_model=UsageView)
async def get_usage(
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> UsageView:
    """Tokens consumed by this tenant.

    Aggregated from the per-turn records rather than a counter, so the figure
    can be reconciled against the rows behind it.
    """
    input_tokens, output_tokens = await repositories.turns.token_usage()
    return UsageView(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _require_role(role: str) -> Role:
    try:
        return Role(role)
    except ValueError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No role named {role!r}. Known roles: "
            + ", ".join(str(r) for r in Role),
        ) from None
