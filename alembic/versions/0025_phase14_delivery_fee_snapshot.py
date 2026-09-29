"""phase14_delivery_fee_snapshot

Phase 14: Add subtotal and delivery_fee snapshot columns to orders table.

Architecture Decision (AQ-01):
  subtotal (items only) + delivery_fee (snapshot from Zone or Location)
  = total_amount

subtotal: server-computed from OrderItem prices, never from client.
delivery_fee: snapshotted at checkout from DeliveryZone.fee or
              Location.delivery_fee. Never mutated after Order creation.
total_amount: subtotal + delivery_fee (application-level invariant).

Data migration: existing orders → subtotal=total_amount, delivery_fee=0.
Backward compat: total_amount unchanged for pre-Phase-14 orders.

Revision ID: 0025
Revises: 0024
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "subtotal",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "delivery_fee",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_orders_subtotal_nonneg",
        "orders",
        "subtotal >= 0",
    )
    op.create_check_constraint(
        "ck_orders_delivery_fee_nonneg",
        "orders",
        "delivery_fee >= 0",
    )
    # Backfill: existing orders have subtotal = total_amount, delivery_fee = 0
    op.execute("UPDATE orders SET subtotal = total_amount, delivery_fee = 0")


def downgrade() -> None:
    op.drop_constraint("ck_orders_delivery_fee_nonneg", "orders", type_="check")
    op.drop_constraint("ck_orders_subtotal_nonneg", "orders", type_="check")
    op.drop_column("orders", "delivery_fee")
    op.drop_column("orders", "subtotal")
