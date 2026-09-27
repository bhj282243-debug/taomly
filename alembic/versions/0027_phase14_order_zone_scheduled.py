"""phase14_order_zone_scheduled

Phase 14: Add delivery_zone_id FK and scheduled_at to orders table.

delivery_zone_id: nullable FK to delivery_zones.
  ON DELETE SET NULL: historical Order survives zone deletion
  (delivery_fee already snapshotted in Order.delivery_fee).

scheduled_at: TIMESTAMPTZ nullable.
  NULL  = immediate order (current behaviour unchanged).
  NOT NULL = scheduled order; status starts at 'new', not 'accepted'.
  Activation loop promotes new → accepted when:
    now >= scheduled_at - preparation_time_minutes - zone.eta_minutes - BUFFER

Revision ID: 0027
Revises: 0026
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "delivery_zone_id",
            sa.BigInteger(),
            sa.ForeignKey("delivery_zones.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "orders",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_orders_delivery_zone",
        "orders",
        ["delivery_zone_id"],
        postgresql_where=sa.text("delivery_zone_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_scheduled_at",
        "orders",
        ["scheduled_at"],
        postgresql_where=sa.text("scheduled_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders_scheduled_at", table_name="orders")
    op.drop_index("ix_orders_delivery_zone", table_name="orders")
    op.drop_column("orders", "scheduled_at")
    op.drop_column("orders", "delivery_zone_id")
