"""The pieces compose, and the composition holds the guarantees.

Module-level tests show each part works. These show they work together — that
a request opens a scope, resolves a model from the tenant's own preferences,
and cannot see another tenant's anything while doing it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from kairos.adapters.persistence.entities import Base, Tenant, Workspace
from kairos.core.catalog import (
    Capability,
    Catalog,
    Endpoint,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    TokenBudget,
    Wire,
)
from kairos.core.catalog.resolution import Role
from kairos.core.tenancy import Role as TenantRole
from kairos.core.tenancy import ScopeNotEstablished, TenantId, TenantScope, UserId, scoped
from kairos.runtime.container import Container
from kairos.runtime.settings import AuthSettings, DatabaseSettings, Deployment, Settings


def model(mid: str, context: int) -> ModelDescriptor:
    return ModelDescriptor(
        id=ModelId(mid),
        remote_id=mid,
        provider=ProviderId("vendor"),
        budget=TokenBudget(context=context, max_output=4_000),
        capabilities=Capability.baseline() | Capability.VISION,
    )


CATALOG = Catalog(
    providers=[
        ProviderDescriptor(
            id=ProviderId("vendor"),
            display_name="Vendor",
            endpoint=Endpoint(wire=Wire.OPENAI_CHAT, credential_env="VENDOR_KEY"),
        )
    ],
    models=[model("flagship", 1_000_000), model("compact", 128_000)],
)


def scope(tenant: str, user: str = "alice") -> TenantScope:
    return TenantScope(
        tenant_id=TenantId(tenant),
        user_id=UserId(user),
        roles=frozenset({TenantRole.MEMBER}),
    )


@pytest.fixture
async def container():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        deployment=Deployment.SOLO,
        database=DatabaseSettings(url="sqlite+aiosqlite:///:memory:"),
        auth=AuthSettings(),
    )
    built = Container(settings, CATALOG, engine=engine)

    async with built.request() as context:
        context.session.add_all(
            [Tenant(id="acme", display_name="Acme"), Tenant(id="rival", display_name="Rival")]
        )

    yield built
    await built.aclose()


class TestAssembly:
    def test_the_pipeline_is_assembled_at_startup(self, container: Container) -> None:
        """An ordering error should stop the process, not the first request."""
        assert len(container.pipeline) > 0

    def test_resilience_settings_reach_the_breaker(self, container: Container) -> None:
        assert container.breaker is not None

    def test_baselines_are_derived_from_the_catalogue(
        self, container: Container
    ) -> None:
        """A deployment cannot name a default it does not have.

        Deriving instead of configuring turns a possible runtime error into an
        impossible one.
        """
        baseline = container.baseline()
        assert baseline[Role.PRIMARY] in CATALOG
        assert baseline[Role.CONDENSE] == "compact"

    def test_cheap_roles_default_to_the_smaller_model(
        self, container: Container
    ) -> None:
        # Condensing and extraction are high-volume and low-judgment; paying
        # flagship rates for them is the easiest way to waste money here.
        baseline = container.baseline()
        assert baseline[Role.CONDENSE] != baseline[Role.PRIMARY]


class TestRequestScope:
    async def test_a_request_yields_working_repositories(
        self, container: Container
    ) -> None:
        with scoped(scope("acme")):
            async with container.request() as context:
                await context.workspaces.add(Workspace(slug="main", title="Main"))

        with scoped(scope("acme")):
            async with container.request() as context:
                assert len(await context.workspaces.list()) == 1

    async def test_a_failed_request_leaves_nothing_behind(
        self, container: Container
    ) -> None:
        """A handler that raises halfway through must not half-write."""
        with pytest.raises(RuntimeError, match="deliberate"), scoped(scope("acme")):
            async with container.request() as context:
                await context.workspaces.add(Workspace(slug="doomed", title="Doomed"))
                raise RuntimeError("deliberate")

        with scoped(scope("acme")):
            async with container.request() as context:
                assert await context.workspaces.by_slug("doomed") is None

    async def test_repositories_still_refuse_to_work_unscoped(
        self, container: Container
    ) -> None:
        # The container does not weaken the guarantee the repository makes.
        with pytest.raises(ScopeNotEstablished):
            async with container.request() as context:
                await context.workspaces.list()


class TestModelSelectionEndToEnd:
    async def test_the_baseline_is_used_when_nothing_is_configured(
        self, container: Container
    ) -> None:
        with scoped(scope("acme")):
            async with container.request() as context:
                choice = await container.select_model(context)
        assert choice.decided_by == "system-baseline"

    async def test_a_tenant_preference_takes_over(self, container: Container) -> None:
        """Read on this request, which is what makes a change take effect now."""
        with scoped(scope("acme")):
            async with container.request() as context:
                await context.preferences.set_role(Role.PRIMARY, "compact")

            async with container.request() as context:
                choice = await container.select_model(context)

        assert choice.model_id == "compact"
        assert choice.decided_by == "tenant-preference"

    async def test_an_explicit_request_overrides_the_preference(
        self, container: Container
    ) -> None:
        with scoped(scope("acme")):
            async with container.request() as context:
                await context.preferences.set_role(Role.PRIMARY, "compact")

            async with container.request() as context:
                choice = await container.select_model(context, explicit="flagship")

        assert choice.model_id == "flagship"

    async def test_one_tenants_preference_does_not_reach_another(
        self, container: Container
    ) -> None:
        """The whole chain, checked at once.

        Preferences are tenant data, read through a scoped repository. If the
        scoping failed anywhere between the middleware and the query, this is
        where it would show.
        """
        with scoped(scope("acme")):
            async with container.request() as context:
                await context.preferences.set_role(Role.PRIMARY, "compact")

        with scoped(scope("rival", user="mallory")):
            async with container.request() as context:
                choice = await container.select_model(context)

        assert choice.decided_by == "system-baseline"
        assert choice.model_id == "flagship"

    async def test_secondary_roles_resolve_independently(
        self, container: Container
    ) -> None:
        with scoped(scope("acme")):
            async with container.request() as context:
                await context.preferences.set_role(Role.PRIMARY, "flagship")

            async with container.request() as context:
                primary = await container.select_model(context, Role.PRIMARY)
                condense = await container.select_model(context, Role.CONDENSE)

        assert primary.model_id == "flagship"
        assert condense.model_id == "compact"


class TestCredentialWiring:
    async def test_tenant_credentials_require_a_decryption_backend(
        self, container: Container
    ) -> None:
        """Left as an explicit gap rather than a silent plaintext path.

        Wiring key management should be an obvious missing step, not something
        that appears to work until someone reads the storage.
        """
        from kairos.adapters.persistence.entities import ProviderCredential

        with scoped(scope("acme")):
            async with container.request() as context:
                await context.credentials.add(
                    ProviderCredential(provider_id="vendor", secret=b"ciphertext")
                )

            async with container.request() as context:
                resolver = container.credentials_for(context)
                with pytest.raises(NotImplementedError, match="key-management"):
                    await resolver.resolve(ProviderId("vendor"))
