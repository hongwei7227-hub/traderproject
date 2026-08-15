"""An in-memory database per test.

SQLite rather than Postgres so the suite has no external dependency. The
entities use portable column types precisely so this works; anything that
depends on a Postgres-only behaviour belongs in the integration suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kairos.adapters.persistence.entities import Base, Tenant
from kairos.core.tenancy import Role, TenantId, TenantScope, UserId


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as active:
        # Two tenants exist in every test. A single-tenant fixture would let
        # an unfiltered query pass by accident, which is the one thing these
        # tests exist to catch.
        active.add_all(
            [
                Tenant(id="acme", display_name="Acme"),
                Tenant(id="rival", display_name="Rival"),
            ]
        )
        await active.flush()
        yield active

    await engine.dispose()


@pytest.fixture
def acme() -> TenantScope:
    return TenantScope(
        tenant_id=TenantId("acme"),
        user_id=UserId("alice"),
        roles=frozenset({Role.MEMBER}),
    )


@pytest.fixture
def rival() -> TenantScope:
    return TenantScope(
        tenant_id=TenantId("rival"),
        user_id=UserId("mallory"),
        roles=frozenset({Role.MEMBER}),
    )


@pytest.fixture
def acme_colleague() -> TenantScope:
    """A second member of the same tenant.

    Tenant scope and owner scope are different questions; a tenant's members
    are not interchangeable.
    """
    return TenantScope(
        tenant_id=TenantId("acme"),
        user_id=UserId("bob"),
        roles=frozenset({Role.MEMBER}),
    )
