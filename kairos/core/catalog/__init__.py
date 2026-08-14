"""The catalogue of reachable models, and the rules for picking one."""

from kairos.core.catalog.descriptors import (
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
from kairos.core.catalog.resolution import (
    DEFAULT_CHAIN,
    ExplicitRequestResolver,
    ModelChoice,
    ModelResolutionChain,
    NoModelAvailable,
    ResolutionRequest,
    Resolver,
    Role,
    SystemBaselineResolver,
    TenantPreferenceResolver,
    WorkspaceDefaultResolver,
)

__all__ = [
    "DEFAULT_CHAIN",
    "Access",
    "Capability",
    "Endpoint",
    "ExplicitRequestResolver",
    "ModelChoice",
    "ModelDescriptor",
    "ModelId",
    "ModelResolutionChain",
    "NoModelAvailable",
    "ProviderDescriptor",
    "ProviderId",
    "ResolutionRequest",
    "Resolver",
    "Role",
    "SystemBaselineResolver",
    "TenantPreferenceResolver",
    "TokenBudget",
    "Wire",
    "WorkspaceDefaultResolver",
]
