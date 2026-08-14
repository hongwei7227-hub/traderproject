"""Initial schema: tenancy, conversation, tenant configuration.

Revision ID: 0001
Revises:
Created: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Portable spellings, matching the entity definitions. Postgres gets its native
# types; the generic fallbacks keep the schema creatable elsewhere.
JSON_DOC = sa.JSON().with_variant(JSONB, "postgresql")


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    # -- tenancy ------------------------------------------------------------

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("settings", JSON_DOC, nullable=False, server_default="{}"),
        *_timestamps(),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("display_name", sa.String(255)),
        sa.Column("email", sa.String(320)),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_member_tenant_user"),
    )
    # Answers "which tenants does this person belong to?", which is how a
    # session is established. The unique constraint above cannot serve it —
    # it leads with the tenant.
    op.create_index("ix_member_user", "members", ["user_id"])

    # -- conversation -------------------------------------------------------

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
    )
    op.create_index(
        "ix_workspace_tenant_owner", "workspaces", ["tenant_id", "owner_id"]
    )

    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Carried directly rather than reached by joining through the
        # workspace: every authorization check reads it, and a join per check
        # is what made the original expensive enough to skip.
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("model_snapshot", sa.String(128)),
        *_timestamps(),
    )
    op.create_index(
        "ix_thread_tenant_workspace", "threads", ["tenant_id", "workspace_id"]
    )
    op.create_index(
        "ix_thread_tenant_owner_recent",
        "threads",
        ["tenant_id", "owner_id", "updated_at"],
    )

    op.create_table(
        "turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "thread_id",
            sa.Uuid(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response", sa.Text()),
        # Recorded per turn rather than aggregated: cost attribution is the
        # question this platform gets asked most, and an aggregate cannot be
        # taken apart afterwards.
        sa.Column("model_id", sa.String(128)),
        sa.Column("provider_id", sa.String(64)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        *_timestamps(),
        sa.CheckConstraint("input_tokens >= 0", name="ck_turn_input_tokens_nonneg"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_turn_output_tokens_nonneg"),
    )
    op.create_index(
        "ix_turn_tenant_thread", "turns", ["tenant_id", "thread_id", "created_at"]
    )

    # -- tenant configuration -----------------------------------------------

    op.create_table(
        "model_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "role", name="uq_model_pref_tenant_role"),
    )

    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        # Encrypted by the application. Never indexed: an index on ciphertext
        # buys nothing and leaks equality.
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column("base_url", sa.String(512)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "provider_id", name="uq_credential_tenant_provider"
        ),
    )

    op.create_table(
        "usage_quotas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_limit", sa.Integer(), nullable=False),
        sa.Column("tokens_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "on_exhaustion", sa.String(16), nullable=False, server_default="reject"
        ),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "period_start", name="uq_quota_tenant_period"),
        sa.CheckConstraint("token_limit > 0", name="ck_quota_limit_positive"),
    )


def downgrade() -> None:
    # Reverse creation order so foreign keys come down before their targets.
    for table in (
        "usage_quotas",
        "provider_credentials",
        "model_preferences",
        "turns",
        "threads",
        "workspaces",
        "members",
        "tenants",
    ):
        op.drop_table(table)
