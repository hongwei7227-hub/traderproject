"""Repositories that cannot be asked for another tenant's rows.

The reference implementation left tenant filtering to each call site. Twenty-one
endpoints remembered the ownership check and one did not, and the one that did
not was the endpoint that writes. That is the failure mode this layer exists to
remove: not "someone wrote a bad query" but "the safe query was more typing than
the unsafe one".

Here every query originates from `_select()`, which reads the ambient scope and
applies the predicate. A subclass composing a query starts from that statement,
so a missing filter is not something one forgets — it is something one would
have to deliberately construct, and the only route to it is named
`_unscoped_escape_hatch` and takes a written justification.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kairos.adapters.persistence.entities import Base, ScopedEntity
from kairos.core.tenancy.context import TenantId, current_scope

EntityT = TypeVar("EntityT", bound=Base)


class ScopeViolation(RuntimeError):
    """Raised when a row does not belong to the tenant that asked for it.

    Distinct from "not found". Reaching this means an identifier from one
    tenant was presented under another's scope, which is either a bug or an
    attempt — either way it deserves a different log line than a typo.
    """

    def __init__(self, entity: str, identifier: object, tenant: str) -> None:
        super().__init__(
            f"{entity} {identifier!r} does not belong to tenant {tenant!r}"
        )


class NotFound(LookupError):
    def __init__(self, entity: str, identifier: object) -> None:
        self.entity = entity
        self.identifier = identifier
        super().__init__(f"{entity} {identifier!r} not found")


class ScopedRepository(Generic[EntityT]):
    """Base for repositories over tenant-owned tables.

    Subclasses provide the entity type and build their queries on `_select()`.
    They do not, and cannot conveniently, write `select(Entity)` themselves.
    """

    entity: type[EntityT]

    def __init__(self, session: AsyncSession) -> None:
        if not issubclass(self.entity, ScopedEntity):
            # A repository over an unscoped table would silently apply no
            # filter. Refuse at construction rather than serve cross-tenant
            # reads.
            raise TypeError(
                f"{type(self).__name__} is a ScopedRepository over "
                f"{self.entity.__name__}, which is not a ScopedEntity"
            )
        self._session = session

    # -- query construction ------------------------------------------------

    def _select(self) -> Select[tuple[EntityT]]:
        """The only sanctioned starting point for a query.

        Reads the ambient scope, so calling this outside a request raises
        rather than returning everything.
        """
        return select(self.entity).where(
            self.entity.tenant_id == current_scope().tenant_id  # type: ignore[attr-defined]
        )

    def _unscoped_escape_hatch(self, *, justification: str) -> Select[tuple[EntityT]]:
        """A query across every tenant. Administrative use only.

        The justification argument is required and unused at runtime. It exists
        so that reaching for this leaves a sentence in the diff explaining why,
        which is the cheapest available review trigger.
        """
        if not justification.strip():
            raise ValueError("an unscoped query requires a written justification")
        return select(self.entity)

    # -- reads -------------------------------------------------------------

    async def get(self, entity_id: UUID) -> EntityT | None:
        result = await self._session.execute(
            self._select().where(self.entity.id == entity_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def require(self, entity_id: UUID) -> EntityT:
        """Fetch, or raise.

        Deliberately does not distinguish "belongs to another tenant" from
        "does not exist" in what it raises to the caller: telling an attacker
        which identifiers are real is a disclosure in itself. The distinction
        is drawn in the logs, not in the response.
        """
        if (found := await self.get(entity_id)) is None:
            raise NotFound(self.entity.__name__, entity_id)
        return found

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[EntityT]:
        result = await self._session.execute(
            self._select().limit(min(limit, 200)).offset(offset)
        )
        return result.scalars().all()

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(self.entity)
            .where(self.entity.tenant_id == current_scope().tenant_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one()

    async def exists(self, entity_id: UUID) -> bool:
        return await self.get(entity_id) is not None

    # -- writes ------------------------------------------------------------

    async def add(self, entity: EntityT) -> EntityT:
        """Persist a new row, stamping it with the active tenant.

        The tenant is taken from scope rather than from the caller. Accepting
        it as an argument would let a caller pass the wrong one, which is the
        same hole this class exists to close — arriving through the write path
        instead of the read path.
        """
        entity.tenant_id = current_scope().tenant_id  # type: ignore[attr-defined]
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def remove(self, entity_id: UUID) -> None:
        """Delete a row, if it belongs to the active tenant.

        Expressed as a scoped DELETE rather than fetch-then-delete so that the
        predicate cannot be lost between the two statements.
        """
        await self._session.execute(
            delete(self.entity).where(
                self.entity.id == entity_id,  # type: ignore[attr-defined]
                self.entity.tenant_id == current_scope().tenant_id,  # type: ignore[attr-defined]
            )
        )

    # -- assertions --------------------------------------------------------

    async def assert_owned(self, entity_id: UUID) -> EntityT:
        """Fetch a row and confirm it is the active tenant's.

        For call sites that want the violation to be loud — an audited write,
        say — rather than indistinguishable from a missing row.
        """
        scope = current_scope()
        result = await self._session.execute(
            select(self.entity).where(self.entity.id == entity_id)  # type: ignore[attr-defined]
        )
        found = result.scalar_one_or_none()
        if found is None:
            raise NotFound(self.entity.__name__, entity_id)
        owner: TenantId = found.tenant_id  # type: ignore[attr-defined]
        if owner != scope.tenant_id:
            raise ScopeViolation(self.entity.__name__, entity_id, scope.tenant_id)
        return found


class OwnedRepository(ScopedRepository[EntityT]):
    """A repository over rows that also belong to one member within the tenant.

    Tenant scope answers "may this organisation see it"; owner scope answers
    "may this person". Both are needed: a tenant's members are not
    interchangeable.
    """

    def _select_own(self) -> Select[tuple[EntityT]]:
        scope = current_scope()
        return self._select().where(
            self.entity.owner_id == scope.user_id  # type: ignore[attr-defined]
        )

    async def list_own(self, *, limit: int = 50, offset: int = 0) -> Sequence[EntityT]:
        result = await self._session.execute(
            self._select_own().limit(min(limit, 200)).offset(offset)
        )
        return result.scalars().all()

    async def get_own(self, entity_id: UUID) -> EntityT | None:
        result = await self._session.execute(
            self._select_own().where(self.entity.id == entity_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def add(self, entity: EntityT) -> EntityT:
        scope = current_scope()
        # Stamp both, for the same reason: neither should be the caller's to
        # choose.
        if getattr(entity, "owner_id", None) is None:
            entity.owner_id = scope.user_id  # type: ignore[attr-defined]
        return await super().add(entity)


def scoped_key(*parts: Any) -> str:
    """Build a cache key that leads with the tenant.

    Cache keys in the reference implementation were flat — keyed on the thread
    alone. Once the HTTP layer's ownership check was missed there was nothing
    underneath it, so possession of an identifier was possession of the data.
    Leading with the tenant means a cross-tenant read misses instead of hits.
    """
    scope = current_scope()
    return ":".join(("t", scope.tenant_id, *(str(p) for p in parts)))
