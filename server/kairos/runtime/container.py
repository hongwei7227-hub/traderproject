"""The composition root.

Every wiring decision the application makes is here, and nowhere else. Modules
below this one take their collaborators as arguments and never reach out to
find them — which is what lets the domain be tested without a database and the
persistence layer without a web server.

The container is built once at startup and holds only things that are safe to
share for the life of the process. Anything request-scoped — a session, a
scope, a unit of work — is created per request and passed down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kairos.adapters.llm.credentials import (
    CredentialResolver,
    EnvironmentKeys,
    TenantKeyStore,
)
from kairos.adapters.persistence.repositories import (
    CredentialRepository,
    ModelPreferenceRepository,
    ThreadRepository,
    TurnRepository,
    WorkspaceRepository,
)
from kairos.core.catalog.registry import Catalog
from kairos.core.catalog.resolution import (
    ModelResolutionChain,
    ResolutionRequest,
    Role,
)
from kairos.core.quota.reservation import QuotaPolicy
from kairos.core.reasoning.pipeline import Pipeline
from kairos.core.reasoning.stages import standard_pipeline
from kairos.core.resilience.breaker import BreakerPolicy, CircuitBreaker
from kairos.core.tenancy.context import current_scope
from kairos.runtime.settings import Settings, get_settings


@dataclass(slots=True)
class RequestContext:
    """Everything one request needs, assembled per request.

    Repositories are built here rather than injected as singletons because
    each is bound to a session, and a session shared between requests would
    let one request see another's uncommitted work.
    """

    session: AsyncSession
    workspaces: WorkspaceRepository
    threads: ThreadRepository
    turns: TurnRepository
    preferences: ModelPreferenceRepository
    credentials: CredentialRepository

    @classmethod
    def of(cls, session: AsyncSession) -> Self:
        return cls(
            session=session,
            workspaces=WorkspaceRepository(session),
            threads=ThreadRepository(session),
            turns=TurnRepository(session),
            preferences=ModelPreferenceRepository(session),
            credentials=CredentialRepository(session),
        )


class RepositoryKeyStore:
    """Adapts the credential repository to the resolver's expectations.

    The resolver knows about tenant keys as a protocol; this is the
    implementation that reads them from the database. Keeping the adaptation
    here rather than in the resolver is what allows the resolver to be tested
    against a dictionary.
    """

    __slots__ = ("_repository",)

    def __init__(self, repository: CredentialRepository) -> None:
        self._repository = repository

    async def get(self, provider):  # type: ignore[no-untyped-def]
        found = await self._repository.for_provider(str(provider))
        if found is None:
            return None
        return (self._decrypt(found.secret), found.base_url)

    async def get_many(self, providers):  # type: ignore[no-untyped-def]
        found = await self._repository.for_providers([str(p) for p in providers])
        return {
            provider: (self._decrypt(cred.secret), cred.base_url)
            for provider, cred in found.items()
        }

    @staticmethod
    def _decrypt(secret: bytes) -> str:
        # Placeholder for the deployment's key-management integration. Left
        # explicit rather than silently returning plaintext, so that wiring a
        # real one is an obvious gap rather than an invisible one.
        raise NotImplementedError(
            "credential decryption is not wired; supply a key-management "
            "backed implementation before serving tenant credentials"
        )


class Container:
    """Process-wide collaborators."""

    __slots__ = (
        "settings",
        "catalog",
        "pipeline",
        "breaker",
        "quota",
        "resolution",
        "_engine",
        "_sessions",
    )

    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog

        # Assembled once, and assembled eagerly: an ordering error in the
        # pipeline should stop the process from starting rather than surface
        # on the first request.
        self.pipeline: Pipeline = standard_pipeline()

        self.breaker = CircuitBreaker(
            BreakerPolicy(
                failure_threshold=settings.resilience.failure_threshold,
                recovery_after_seconds=settings.resilience.recovery_after_seconds,
                success_threshold=settings.resilience.success_threshold,
            )
        )
        self.quota = QuotaPolicy()
        self.resolution = ModelResolutionChain()

        # Built on first use rather than here. Constructing an application
        # object should not require a database driver to be installed or a
        # database to be reachable — a process must be able to come up far
        # enough to report that it cannot reach its database.
        self._engine = engine
        self._sessions = (
            async_sessionmaker(engine, expire_on_commit=False) if engine else None
        )

    @classmethod
    def build(cls, catalog: Catalog, settings: Settings | None = None) -> Self:
        return cls(settings or get_settings(), catalog)

    # -- request scope -----------------------------------------------------

    def _session_factory(self):  # type: ignore[no-untyped-def]
        if self._sessions is None:
            self._engine = create_async_engine(
                self.settings.database.url,
                pool_size=self.settings.database.pool_size,
                max_overflow=self.settings.database.max_overflow,
                pool_timeout=self.settings.database.pool_timeout_seconds,
                echo=self.settings.database.echo_sql,
            )
            self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        return self._sessions

    @asynccontextmanager
    async def request(self) -> AsyncIterator[RequestContext]:
        """One session, one transaction, one request.

        Committed on success and rolled back on any exception, so a handler
        that raises halfway through cannot leave a partial write behind.
        """
        async with self._session_factory()() as session:
            context = RequestContext.of(session)
            try:
                yield context
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    def credentials_for(self, context: RequestContext) -> CredentialResolver:
        return CredentialResolver(
            self.catalog,
            RepositoryKeyStore(context.credentials),
            EnvironmentKeys(),
        )

    # -- model selection ---------------------------------------------------

    async def select_model(
        self,
        context: RequestContext,
        role: str = Role.PRIMARY,
        *,
        explicit: str | None = None,
    ):  # type: ignore[no-untyped-def]
        """Resolve the model for a role, under the current tenant scope.

        The tenant's preferences are read here, on this request, which is what
        makes a change take effect immediately rather than at the next deploy.
        """
        scope = current_scope()
        preferences = await context.preferences.as_mapping()

        return self.resolution.resolve(
            ResolutionRequest(
                role=role,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                explicit=explicit,  # type: ignore[arg-type]
                tenant_preferences=preferences,  # type: ignore[arg-type]
                baseline=self.baseline(),
            )
        )

    def baseline(self) -> dict[str, str]:
        """The platform's own defaults, derived from what the catalogue offers.

        Derived rather than configured so that a deployment cannot name a
        default it does not actually have — the mistake that turns a startup
        error into a runtime one.

        Public because the configuration endpoints resolve roles the same way
        the engine does, and a tenant reading which model serves a role should
        get the answer from the same source that decides it.
        """
        selectable = self.catalog.selectable()
        if not selectable:
            return {}
        primary = selectable[0].id
        cheapest = min(selectable, key=lambda m: m.budget.context).id
        return {
            Role.PRIMARY: primary,
            Role.SWIFT: cheapest,
            Role.CONDENSE: cheapest,
            Role.EXTRACT: cheapest,
        }

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()


@asynccontextmanager
async def lifespan(container: Container) -> AsyncIterator[Container]:
    """Hold a container for the life of the application."""
    try:
        yield container
    finally:
        await container.aclose()


def default_catalog_loader() -> Callable[[], Catalog]:
    """Where a deployment plugs its own catalogue in.

    Returned as a callable rather than a value so that loading can do I/O — a
    catalogue may come from a file, a database, or a provider's own model
    listing endpoint.
    """

    def load() -> Catalog:
        return Catalog(providers=[], models=[])

    return load
