"""Persistence models.

Tenant-owned rows carry `tenant_id` and inherit from `ScopedEntity`, which is
what the repository layer keys its filtering off. The marker is not decoration:
a table that should be tenant-scoped but does not inherit it will not be
filtered, so the inheritance is the declaration.

Every scoped table carries a composite index leading with `tenant_id`. Queries
always filter on it, so it belongs first in the index rather than appended to
the end of one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Portable spellings. Postgres is the deployment target and gets its native
# types; the generic fallbacks let the suite run against in-memory SQLite so
# that testing persistence does not require standing up a database.
JsonDoc = JSON().with_variant(JSONB, "postgresql")
PrimaryKey = Uuid()


class Base(DeclarativeBase):
    """Declarative base for every persisted entity."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ScopedEntity:
    """Marks a table whose rows belong to exactly one tenant.

    The repository layer refuses to build a query for one of these without a
    tenant predicate. Inheriting from this is therefore a statement that the
    table must never be read across tenants.

    No index is declared here. Every scoped table needs one leading with
    `tenant_id`, but each already has a composite index or unique constraint
    that starts there and serves the same lookups — a standalone index on top
    would be redundant, and redundant indexes are paid for on every write. The
    requirement is asserted in the architecture suite instead, so a new table
    that satisfies it differently still satisfies it.
    """

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


class Tenant(Base, TimestampMixin):
    """A billing and isolation boundary.

    Not a synonym for a company: a single person self-hosting is a tenant of
    one. What makes it a tenant is that quota, credentials and data are scoped
    to it.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    settings: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)

    members: Mapped[list[Member]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class Member(Base, TimestampMixin):
    """A principal's membership in a tenant.

    Identity is external — the subject claim from whatever issued the token —
    so the id is a string rather than a generated UUID. A person belonging to
    several tenants has one row per membership, which is what lets roles differ
    between them.
    """

    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_member_tenant_user"),
        Index("ix_member_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    display_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))

    tenant: Mapped[Tenant] = relationship(back_populates="members")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Workspace(Base, ScopedEntity, TimestampMixin):
    """A durable working context: files, memory, and the threads held in it."""

    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspace_tenant_owner", "tenant_id", "owner_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    threads: Mapped[list[Thread]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Thread(Base, ScopedEntity, TimestampMixin):
    """One conversation.

    Ownership is carried directly rather than inferred by joining through the
    workspace. The reference implementation resolved it with a join on every
    authorization check, which made the check expensive enough that call sites
    were tempted to skip it — and one of them did.
    """

    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_thread_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_thread_tenant_owner_recent", "tenant_id", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    model_snapshot: Mapped[str | None] = mapped_column(
        String(128),
        comment=(
            "Model in force when the thread began. Kept so that switching "
            "models does not retroactively reinterpret history a different "
            "model produced."
        ),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="threads")
    turns: Mapped[list[Turn]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Turn(Base, ScopedEntity, TimestampMixin):
    """One exchange: a request in, a response out, and what it cost."""

    __tablename__ = "turns"
    __table_args__ = (
        Index("ix_turn_tenant_thread", "tenant_id", "thread_id", "created_at"),
        CheckConstraint("input_tokens >= 0", name="ck_turn_input_tokens_nonneg"),
        CheckConstraint("output_tokens >= 0", name="ck_turn_output_tokens_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TurnStatus.RUNNING
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text)

    # Recorded per turn rather than aggregated, because cost attribution is
    # the question this platform gets asked most and an aggregate cannot be
    # taken apart afterwards.
    model_id: Mapped[str | None] = mapped_column(String(128))
    provider_id: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text)

    thread: Mapped[Thread] = relationship(back_populates="turns")


# ---------------------------------------------------------------------------
# Tenant configuration
# ---------------------------------------------------------------------------


class ModelPreference(Base, ScopedEntity, TimestampMixin):
    """A tenant's model choice for one role.

    Read on every request rather than cached at process start. That is what
    makes a model change take effect on the next request instead of the next
    deploy.
    """

    __tablename__ = "model_preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role", name="uq_model_pref_tenant_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)


class ProviderCredential(Base, ScopedEntity, TimestampMixin):
    """A tenant's own key for one provider.

    The secret is stored encrypted and never indexed. `base_url` is here
    because a tenant supplying its own key often supplies its own endpoint
    with it — a private deployment, a regional gateway.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_id", name="uq_credential_tenant_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    secret: Mapped[bytes] = mapped_column(nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageQuota(Base, ScopedEntity, TimestampMixin):
    """A tenant's token allowance for the current period.

    Held in the database as the durable record; the hot path reserves against
    a cache and settles here. Tokens are counted rather than requests because
    an agent turn's cost varies by orders of magnitude between requests.
    """

    __tablename__ = "usage_quotas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_start", name="uq_quota_tenant_period"),
        CheckConstraint("token_limit > 0", name="ck_quota_limit_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PrimaryKey, primary_key=True, default=uuid.uuid4
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_limit: Mapped[int] = mapped_column(nullable=False)
    tokens_consumed: Mapped[int] = mapped_column(nullable=False, default=0)
    on_exhaustion: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="reject",
        comment="reject | degrade | allow — what to do when the allowance runs out.",
    )
