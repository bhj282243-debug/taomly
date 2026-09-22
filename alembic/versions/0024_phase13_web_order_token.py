"""phase13_web_order_token

Phase 13: Add web_order_token_hash to orders for anonymous web order tracking.

Stores SHA-256 hex digest of the raw token (never the raw token itself).
Nullable: NULL for Telegram orders and legacy orders.
Partial unique index: allows multiple NULLs, enforces uniqueness among non-NULL values.

Revision ID: 0024
Revises: 0023
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("web_order_token_hash", sa.String(64), nullable=True),
    )
    # Partial unique index: uniqueness only among non-NULL hashes.
    # Allows multiple NULL values (one per Telegram/legacy order).
    op.create_index(
        "ix_orders_web_order_token_hash",
        "orders",
        ["web_order_token_hash"],
        unique=True,
        postgresql_where=sa.text("web_order_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_orders_web_order_token_hash",
        table_name="orders",
        postgresql_where=sa.text("web_order_token_hash IS NOT NULL"),
    )
    op.drop_column("orders", "web_order_token_hash")
