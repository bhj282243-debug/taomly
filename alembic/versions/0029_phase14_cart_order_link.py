"""phase14_cart_order_link

Phase 14: Add order_id FK to carts table (SEC-03 idempotency fix).

Direct cart → order link established atomically at checkout (Step 11,
same transaction as cart.status='checked_out').

Replaces heuristic lookup (restaurant_id + currency) with exact FK.
ON DELETE SET NULL: Order deletion does not cascade to Cart.
Nullable: pre-Phase-14 carts have order_id=NULL; legacy lookup preserved
as fallback for those carts only.

Revision ID: 0029
Revises: 0028
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "carts",
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_carts_order_id",
        "carts",
        ["order_id"],
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_carts_order_id", table_name="carts")
    op.drop_column("carts", "order_id")
