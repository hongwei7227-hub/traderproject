"""Tenant isolation, exercised against a real database.

These are the tests that would have caught the reference implementation's
authorization hole. Not by checking that some endpoint remembers to call a
guard — by checking that the data layer cannot be persuaded to return another
tenant's rows even when asked directly for them by id.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kairos.adapters.persistence.entities import ScopedEntity, Tenant, Thread, Workspace
from kairos.adapters.persistence.repositories import (
    CredentialRepository,
    ModelPreferenceRepository,
    ThreadRepository,
    WorkspaceRepository,
)
from kairos.adapters.persistence.repository import (
    NotFound,
    ScopedRepository,
    ScopeViolation,
    scoped_key,
)
from kairos.core.tenancy import ScopeNotEstablished, TenantScope, scoped


async def make_workspace(
    session: AsyncSession, scope: TenantScope, slug: str = "main"
) -> Workspace:
    with scoped(scope):
        repo = WorkspaceRepository(session)
        workspace = await repo.add(Workspace(slug=slug, title=slug.title()))
    await session.flush()
    return workspace


class TestCrossTenantReads:
    async def test_another_tenants_row_is_invisible_by_id(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        """The core property. Holding an id is not holding the data."""
        theirs = await make_workspace(session, acme)

        with scoped(rival):
            assert await WorkspaceRepository(session).get(theirs.id) is None

    async def test_another_tenants_row_is_absent_from_listings(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        await make_workspace(session, acme, "acme-ws")
        await make_workspace(session, rival, "rival-ws")

        with scoped(rival):
            visible = await WorkspaceRepository(session).list()

        assert [w.slug for w in visible] == ["rival-ws"]

    async def test_counts_are_scoped(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        await make_workspace(session, acme, "a1")
        await make_workspace(session, acme, "a2")
        await make_workspace(session, rival, "r1")

        with scoped(acme):
            assert await WorkspaceRepository(session).count() == 2
        with scoped(rival):
            assert await WorkspaceRepository(session).count() == 1

    async def test_a_derived_query_inherits_the_filter(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        """Subclass queries build on the scoped statement, so they stay scoped.

        This is what makes the pattern hold as the codebase grows: a new
        finder method is filtered because of where it starts, not because its
        author remembered.
        """
        await make_workspace(session, acme, "shared-slug")

        with scoped(rival):
            assert await WorkspaceRepository(session).by_slug("shared-slug") is None

    async def test_require_raises_not_found_rather_than_disclosing(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        # Distinguishing "exists but is not yours" from "does not exist" tells
        # an attacker which identifiers are real.
        theirs = await make_workspace(session, acme)

        with scoped(rival), pytest.raises(NotFound):
            await WorkspaceRepository(session).require(theirs.id)


class TestCrossTenantWrites:
    async def test_the_tenant_stamp_comes_from_scope_not_the_caller(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        """A caller cannot choose which tenant owns what it writes.

        The write path is where the reference implementation's hole actually
        was, so it gets the same treatment as the read path.
        """
        with scoped(acme):
            written = await WorkspaceRepository(session).add(
                Workspace(tenant_id="rival", slug="smuggled", title="Smuggled")
            )

        assert written.tenant_id == "acme"

    async def test_deleting_another_tenants_row_does_nothing(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        theirs = await make_workspace(session, acme)

        with scoped(rival):
            await WorkspaceRepository(session).remove(theirs.id)
        await session.flush()

        with scoped(acme):
            assert await WorkspaceRepository(session).get(theirs.id) is not None

    async def test_owner_is_stamped_from_scope(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        workspace = await make_workspace(session, acme)
        assert workspace.owner_id == "alice"


class TestLoudViolations:
    async def test_assert_owned_distinguishes_a_violation_from_a_miss(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        """Audited call sites want the difference; responses still must not.

        `require` hides it. `assert_owned` surfaces it, for the paths that
        should log an attempt rather than a typo.
        """
        theirs = await make_workspace(session, acme)

        with scoped(rival), pytest.raises(ScopeViolation):
            await WorkspaceRepository(session).assert_owned(theirs.id)

    async def test_a_genuinely_missing_row_is_not_a_violation(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme), pytest.raises(NotFound):
            await WorkspaceRepository(session).assert_owned(uuid.uuid4())


class TestOwnerScope:
    async def test_colleagues_share_the_tenant_but_not_the_rows(
        self, session: AsyncSession, acme: TenantScope, acme_colleague: TenantScope
    ) -> None:
        """Tenant scope answers 'may this organisation'; owner scope 'may this person'."""
        await make_workspace(session, acme, "alices")

        with scoped(acme_colleague):
            repo = WorkspaceRepository(session)
            assert [w.slug for w in await repo.list()] == ["alices"]
            assert await repo.list_own() == []


class TestScopeIsMandatory:
    async def test_a_query_without_scope_raises(self, session: AsyncSession) -> None:
        # Not "returns everything". The absence of scope is a routing bug and
        # must not be answerable.
        with pytest.raises(ScopeNotEstablished):
            await WorkspaceRepository(session).list()

    async def test_a_write_without_scope_raises(self, session: AsyncSession) -> None:
        with pytest.raises(ScopeNotEstablished):
            await WorkspaceRepository(session).add(Workspace(slug="x", title="X"))

    def test_a_repository_over_an_unscoped_table_is_refused(
        self, session: AsyncSession
    ) -> None:
        """Otherwise it would apply no filter and nobody would notice."""

        class TenantRepository(ScopedRepository[Tenant]):
            entity = Tenant

        assert not issubclass(Tenant, ScopedEntity)
        with pytest.raises(TypeError, match="not a ScopedEntity"):
            TenantRepository(session)


class TestEscapeHatch:
    async def test_an_unscoped_query_demands_a_justification(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            repo = WorkspaceRepository(session)
            with pytest.raises(ValueError, match="justification"):
                repo._unscoped_escape_hatch(justification="   ")

    async def test_a_justified_unscoped_query_crosses_tenants(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        # It has to actually work — an escape hatch nobody can use gets
        # replaced by a raw session call that no reviewer will notice.
        await make_workspace(session, acme, "a")
        await make_workspace(session, rival, "r")

        with scoped(acme):
            repo = WorkspaceRepository(session)
            statement = repo._unscoped_escape_hatch(justification="admin usage report")
            result = await session.execute(statement)

        assert len(result.scalars().all()) == 2


class TestCacheKeys:
    def test_keys_lead_with_the_tenant(self, acme: TenantScope) -> None:
        """Defence in depth: a cross-tenant read misses instead of hitting.

        The reference implementation keyed on the thread alone, so once the
        HTTP check was missed there was nothing underneath it.
        """
        with scoped(acme):
            assert scoped_key("stream", "run-1") == "t:acme:stream:run-1"

    def test_the_same_logical_key_differs_between_tenants(
        self, acme: TenantScope, rival: TenantScope
    ) -> None:
        with scoped(acme):
            ours = scoped_key("thread", "shared-id")
        with scoped(rival):
            theirs = scoped_key("thread", "shared-id")
        assert ours != theirs

    def test_building_a_key_without_scope_raises(self) -> None:
        with pytest.raises(ScopeNotEstablished):
            scoped_key("thread", "x")


class TestPreferencesAndCredentials:
    async def test_preferences_are_per_tenant(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        with scoped(acme):
            await ModelPreferenceRepository(session).set_role("primary", "big-model")
        await session.flush()

        with scoped(rival):
            assert await ModelPreferenceRepository(session).as_mapping() == {}

    async def test_setting_a_role_twice_updates_rather_than_duplicates(
        self, session: AsyncSession, acme: TenantScope
    ) -> None:
        with scoped(acme):
            repo = ModelPreferenceRepository(session)
            await repo.set_role("primary", "first")
            await session.flush()
            await repo.set_role("primary", "second")
            await session.flush()
            assert await repo.as_mapping() == {"primary": "second"}

    async def test_credentials_never_leak_across_tenants(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        from kairos.adapters.persistence.entities import ProviderCredential

        with scoped(acme):
            await CredentialRepository(session).add(
                ProviderCredential(provider_id="vendor", secret=b"acme-secret")
            )
        await session.flush()

        with scoped(rival):
            repo = CredentialRepository(session)
            assert await repo.for_provider("vendor") is None
            assert await repo.configured_providers() == ()


class TestThreadScoping:
    async def test_threads_are_scoped_to_their_tenant(
        self, session: AsyncSession, acme: TenantScope, rival: TenantScope
    ) -> None:
        workspace = await make_workspace(session, acme)

        with scoped(acme):
            thread = await ThreadRepository(session).add(
                Thread(workspace_id=workspace.id, title="Ours")
            )
        await session.flush()

        # The endpoint the reference implementation left unguarded took a
        # thread id straight from the URL. Here that is not enough.
        with scoped(rival):
            assert await ThreadRepository(session).get(thread.id) is None
