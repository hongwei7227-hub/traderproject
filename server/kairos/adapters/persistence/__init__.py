"""Database-backed implementations of the core's persistence ports."""

from kairos.adapters.persistence.entities import (
    Base,
    Member,
    ModelPreference,
    ProviderCredential,
    ScopedEntity,
    Tenant,
    Thread,
    Turn,
    TurnStatus,
    UsageQuota,
    Workspace,
)
from kairos.adapters.persistence.repositories import (
    CredentialRepository,
    ModelPreferenceRepository,
    ThreadRepository,
    TurnRepository,
    WorkspaceRepository,
)
from kairos.adapters.persistence.repository import (
    NotFound,
    OwnedRepository,
    ScopedRepository,
    ScopeViolation,
    scoped_key,
)

__all__ = [
    "Base",
    "CredentialRepository",
    "Member",
    "ModelPreference",
    "ModelPreferenceRepository",
    "NotFound",
    "OwnedRepository",
    "ProviderCredential",
    "ScopeViolation",
    "ScopedEntity",
    "ScopedRepository",
    "Tenant",
    "Thread",
    "ThreadRepository",
    "Turn",
    "TurnRepository",
    "TurnStatus",
    "UsageQuota",
    "Workspace",
    "WorkspaceRepository",
    "scoped_key",
]
