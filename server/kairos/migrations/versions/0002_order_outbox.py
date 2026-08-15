"""Order outbox: proposals awaiting delivery to the execution worker.

Revision ID: 0002
Revises: 0001
Created: 2026-08-16

Only the outbox is created here. The orders themselves live in the execution
worker's own schema, created by the worker's own migrations — this platform
reads that table and must never create it, or two migration histories end up
with different opinions about the same columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOC = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "order_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("destination", sa.String(128), nullable=False),
        sa.Column(
            "ordering_key",
            sa.String(64),
            nullable=False,
            comment=(
                "Account id. Orders for one account must reach the worker in "
                "order, or a cancel can overtake the buy it was cancelling."
            ),
        ),
        sa.Column("payload", JSON_DOC, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Half of the worker's idempotency key. The constraint is what makes a
        # retried submission collapse into the original row instead of placing
        # a second order.
        sa.UniqueConstraint(
            "tenant_id", "proposal_id", name="uq_outbox_tenant_proposal"
        ),
    )
    # The relay's query. Single-column because the relay sweeps across tenants
    # and so cannot lead with one.
    op.create_index("ix_outbox_status", "order_outbox", ["status"])


def downgrade() -> None:
    op.drop_index("ix_outbox_status", table_name="order_outbox")
    op.drop_table("order_outbox")
