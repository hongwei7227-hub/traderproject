"""Deciding which model serves a given role on a given request.

The reference implementation resolved this in one function of about a thousand
lines that also handled feature flags, search preferences, crawl gating and
compaction profiles. Adding a precedence level meant editing the middle of it.

Here each precedence level is a resolver that either answers or declines. The
chain runs in order and the first answer wins. Adding a level is adding a class;
reordering is reordering a tuple; testing one level does not require standing up
the other four.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kairos.core.catalog.descriptors import Capability, ModelId
from kairos.core.tenancy.context import TenantId, UserId


class Role(StrEnum):
    """What a model is being asked to do within a single turn.

    Splitting this matters for cost. Condensing history and pulling the text
    out of a web page are high-volume, low-judgment jobs; paying flagship rates
    for them is the single easiest way to waste money on an agent platform.
    """

    PRIMARY = "primary"
    SWIFT = "swift"
    CONDENSE = "condense"
    EXTRACT = "extract"

    @classmethod
    def delegate(cls, name: str) -> str:
        """Role key for a named sub-agent."""
        return f"delegate:{name}"

    def requires(self) -> Capability:
        """Capabilities a model must have to fill this role."""
        if self is Role.PRIMARY:
            return Capability.baseline() | Capability.VISION
        return Capability.baseline()


@dataclass(frozen=True, slots=True)
class ResolutionRequest:
    """Everything a resolver may consider."""

    role: str
    tenant_id: TenantId
    user_id: UserId
    explicit: ModelId | None = None
    tenant_preferences: Mapping[str, ModelId] = field(default_factory=dict)
    workspace_defaults: Mapping[str, ModelId] = field(default_factory=dict)
    baseline: Mapping[str, ModelId] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A resolved model, and why it was chosen.

    The provenance is not decoration. When a tenant asks why their request went
    to a different model than the one they configured, the answer has to come
    from somewhere other than reading the resolver chain by hand.
    """

    model_id: ModelId
    decided_by: str

    def __str__(self) -> str:
        return f"{self.model_id} (via {self.decided_by})"


@runtime_checkable
class Resolver(Protocol):
    """One precedence level.

    Returning ``None`` means "not my call" and passes the decision down. It does
    not mean "no model" — only the end of the chain can conclude that.
    """

    @property
    def name(self) -> str: ...

    def resolve(self, request: ResolutionRequest) -> ModelId | None: ...


class ExplicitRequestResolver:
    """A model named on the request itself.

    Highest precedence: the caller asked for this one specifically, usually
    because a person picked it from a menu a moment ago.
    """

    name = "explicit-request"

    def resolve(self, request: ResolutionRequest) -> ModelId | None:
        # Only the primary role is addressable per request. Letting a caller
        # redirect the condensing model would let them shift cost onto a role
        # the tenant never chose.
        if request.role != Role.PRIMARY:
            return None
        return request.explicit


class TenantPreferenceResolver:
    """What this tenant configured.

    Read fresh on every request rather than cached at startup, which is what
    makes model switching take effect without a restart.
    """

    name = "tenant-preference"

    def resolve(self, request: ResolutionRequest) -> ModelId | None:
        return request.tenant_preferences.get(request.role)


class WorkspaceDefaultResolver:
    """What this workspace configured, for tenants that scope settings per workspace."""

    name = "workspace-default"

    def resolve(self, request: ResolutionRequest) -> ModelId | None:
        return request.workspace_defaults.get(request.role)


class SystemBaselineResolver:
    """The platform's own default.

    Last stop. If a role has no baseline the chain has nothing left to offer,
    which is a deployment configuration error rather than a tenant one.
    """

    name = "system-baseline"

    def resolve(self, request: ResolutionRequest) -> ModelId | None:
        return request.baseline.get(request.role)


DEFAULT_CHAIN: tuple[Resolver, ...] = (
    ExplicitRequestResolver(),
    TenantPreferenceResolver(),
    WorkspaceDefaultResolver(),
    SystemBaselineResolver(),
)


class NoModelAvailable(RuntimeError):
    """Raised when no resolver could name a model for a role."""

    def __init__(self, role: str, tried: Sequence[str]) -> None:
        self.role = role
        self.tried = tuple(tried)
        super().__init__(
            f"No model resolved for role {role!r}; consulted: {', '.join(tried)}"
        )


class ModelResolutionChain:
    """Runs resolvers in order and reports which one decided."""

    __slots__ = ("_resolvers",)

    def __init__(self, resolvers: Sequence[Resolver] = DEFAULT_CHAIN) -> None:
        if not resolvers:
            raise ValueError("resolution chain cannot be empty")
        self._resolvers = tuple(resolvers)

    def resolve(self, request: ResolutionRequest) -> ModelChoice:
        for resolver in self._resolvers:
            if (model_id := resolver.resolve(request)) is not None:
                return ModelChoice(model_id=model_id, decided_by=resolver.name)
        raise NoModelAvailable(request.role, [r.name for r in self._resolvers])

    def resolve_or_none(self, request: ResolutionRequest) -> ModelChoice | None:
        """As `resolve`, but returns None instead of raising.

        For optional roles: a deployment that has not configured a separate
        condensing model should fall back to the primary one, not fail.
        """
        try:
            return self.resolve(request)
        except NoModelAvailable:
            return None
