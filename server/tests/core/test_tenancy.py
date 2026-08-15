"""The tenant scope must fail loudly when absent, and must not leak."""

from __future__ import annotations

import asyncio

import pytest

from kairos.core.tenancy import (
    Role,
    ScopeNotEstablished,
    TenantId,
    TenantScope,
    UserId,
    current_scope,
    current_tenant,
    scope_or_none,
    scoped,
)


def make_scope(tenant: str = "acme", user: str = "u-1") -> TenantScope:
    return TenantScope(
        tenant_id=TenantId(tenant),
        user_id=UserId(user),
        roles=frozenset({Role.MEMBER}),
    )


class TestAbsentScope:
    def test_reading_without_scope_raises(self) -> None:
        # The alternative — returning None — invites `if scope:` and a silent
        # unscoped query. Absence is a routing bug and must surface as one.
        with pytest.raises(ScopeNotEstablished):
            current_scope()

    def test_soft_accessor_returns_none(self) -> None:
        assert scope_or_none() is None


class TestScopeLifetime:
    def test_scope_is_visible_inside_the_block(self) -> None:
        scope = make_scope()
        with scoped(scope):
            assert current_scope() is scope
            assert current_tenant() == "acme"

    def test_scope_is_gone_after_the_block(self) -> None:
        with scoped(make_scope()):
            pass
        assert scope_or_none() is None

    def test_scope_is_restored_after_an_exception(self) -> None:
        with pytest.raises(RuntimeError, match="boom"), scoped(make_scope()):
            raise RuntimeError("boom")
        assert scope_or_none() is None

    def test_nesting_restores_the_outer_scope(self) -> None:
        outer, inner = make_scope("outer"), make_scope("inner")
        with scoped(outer):
            with scoped(inner):
                assert current_tenant() == "inner"
            assert current_tenant() == "outer"


class TestConcurrentIsolation:
    async def test_tasks_do_not_observe_each_others_scope(self) -> None:
        """Two tenants served concurrently must not see each other.

        This is the property that makes ambient scope safe to rely on. Context
        variables are copied per task, so a scope set in one task is invisible
        to its siblings — but only if nothing reaches across with a plain
        global, so the guarantee is worth pinning down.
        """
        observed: dict[str, str] = {}

        async def serve(tenant: str, delay: float) -> None:
            with scoped(make_scope(tenant)):
                await asyncio.sleep(delay)
                observed[tenant] = current_tenant()

        # Staggered so the tasks interleave rather than run to completion in turn.
        await asyncio.gather(serve("alpha", 0.02), serve("beta", 0.01))

        assert observed == {"alpha": "alpha", "beta": "beta"}

    async def test_scope_does_not_leak_into_a_sibling_task(self) -> None:
        leaked: list[str | None] = []

        async def unscoped_worker() -> None:
            scope = scope_or_none()
            leaked.append(scope.tenant_id if scope else None)

        async def scoped_worker() -> None:
            with scoped(make_scope("acme")):
                await asyncio.sleep(0.01)

        await asyncio.gather(scoped_worker(), unscoped_worker())

        assert leaked == [None]


class TestRoles:
    def test_role_membership(self) -> None:
        scope = make_scope()
        assert scope.has_role(Role.MEMBER)
        assert not scope.has_role(Role.OWNER)

    def test_service_principals_are_identifiable(self) -> None:
        human = make_scope()
        machine = TenantScope(
            tenant_id=TenantId("acme"),
            user_id=UserId("svc-ingest"),
            roles=frozenset({Role.SERVICE}),
        )
        assert not human.is_service()
        assert machine.is_service()

    def test_scope_is_immutable(self) -> None:
        # A scope that could be mutated mid-request would make every
        # authorization check a time-of-check-to-time-of-use question.
        scope = make_scope()
        with pytest.raises(AttributeError):
            scope.tenant_id = TenantId("other")  # type: ignore[misc]
