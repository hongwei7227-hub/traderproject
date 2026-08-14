"""Concrete repositories.

Each is thin. The interesting behaviour — that a query cannot escape its
tenant — lives in the base class, and these only add the queries their callers
actually need.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from kairos.adapters.persistence.entities import (
    ModelPreference,
    ProviderCredential,
    Thread,
    Turn,
    Workspace,
)
from kairos.adapters.persistence.repository import OwnedRepository, ScopedRepository
from kairos.core.tenancy.context import current_scope


class WorkspaceRepository(OwnedRepository[Workspace]):
    entity = Workspace

    async def by_slug(self, slug: str) -> Workspace | None:
        result = await self._session.execute(self._select().where(Workspace.slug == slug))
        return result.scalar_one_or_none()

    async def active(self, *, limit: int = 50) -> Sequence[Workspace]:
        result = await self._session.execute(
            self._select()
            .where(Workspace.archived_at.is_(None))
            .order_by(Workspace.updated_at.desc())
            .limit(min(limit, 200))
        )
        return result.scalars().all()


class ThreadRepository(OwnedRepository[Thread]):
    entity = Thread

    async def in_workspace(
        self, workspace_id: UUID, *, limit: int = 50
    ) -> Sequence[Thread]:
        result = await self._session.execute(
            self._select()
            .where(Thread.workspace_id == workspace_id)
            .order_by(Thread.updated_at.desc())
            .limit(min(limit, 200))
        )
        return result.scalars().all()

    async def recent(self, *, limit: int = 20) -> Sequence[Thread]:
        result = await self._session.execute(
            self._select_own().order_by(Thread.updated_at.desc()).limit(min(limit, 200))
        )
        return result.scalars().all()


class TurnRepository(ScopedRepository[Turn]):
    entity = Turn

    async def for_thread(self, thread_id: UUID, *, limit: int = 100) -> Sequence[Turn]:
        result = await self._session.execute(
            self._select()
            .where(Turn.thread_id == thread_id)
            .order_by(Turn.created_at)
            .limit(min(limit, 500))
        )
        return result.scalars().all()

    async def token_usage(self) -> tuple[int, int]:
        """Input and output tokens consumed by this tenant.

        Aggregated from the per-turn record rather than a running counter, so
        that the number can always be reconciled against the rows behind it.
        """
        from sqlalchemy import func

        result = await self._session.execute(
            select(
                func.coalesce(func.sum(Turn.input_tokens), 0),
                func.coalesce(func.sum(Turn.output_tokens), 0),
            ).where(Turn.tenant_id == current_scope().tenant_id)
        )
        row = result.one()
        return int(row[0]), int(row[1])


class ModelPreferenceRepository(ScopedRepository[ModelPreference]):
    entity = ModelPreference

    async def as_mapping(self) -> dict[str, str]:
        """Every role this tenant has configured.

        Read on each request. Returning a plain mapping keeps the resolution
        chain free of any knowledge that preferences come from a database.
        """
        result = await self._session.execute(self._select())
        return {pref.role: pref.model_id for pref in result.scalars()}

    async def set_role(self, role: str, model_id: str) -> None:
        result = await self._session.execute(
            self._select().where(ModelPreference.role == role)
        )
        if (existing := result.scalar_one_or_none()) is not None:
            existing.model_id = model_id
            return
        await self.add(ModelPreference(role=role, model_id=model_id))

    async def clear_role(self, role: str) -> None:
        result = await self._session.execute(
            self._select().where(ModelPreference.role == role)
        )
        if (existing := result.scalar_one_or_none()) is not None:
            await self._session.delete(existing)


class CredentialRepository(ScopedRepository[ProviderCredential]):
    entity = ProviderCredential

    async def for_provider(self, provider_id: str) -> ProviderCredential | None:
        result = await self._session.execute(
            self._select().where(ProviderCredential.provider_id == provider_id)
        )
        return result.scalar_one_or_none()

    async def for_providers(
        self, provider_ids: Sequence[str]
    ) -> dict[str, ProviderCredential]:
        """Fetch several at once.

        A single request may need credentials for the primary model, each
        secondary role and every fallback. Resolving those one at a time is
        several round trips on the latency-critical path.
        """
        if not provider_ids:
            return {}
        result = await self._session.execute(
            self._select().where(ProviderCredential.provider_id.in_(provider_ids))
        )
        return {cred.provider_id: cred for cred in result.scalars()}

    async def configured_providers(self) -> tuple[str, ...]:
        result = await self._session.execute(self._select())
        return tuple(sorted(cred.provider_id for cred in result.scalars()))
