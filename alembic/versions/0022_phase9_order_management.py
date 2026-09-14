"""
Phase 9: Order Management

Revision ID: 0022
Revises: 0021

Changes:
    A. orders.cancellation_reason — TEXT NULL
       Reason supplied by restaurant admin when cancelling an order.
       NOT a payment/refund reason.
       Stored only when status transitions to 'cancelled'.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── A. orders.cancellation_reason ─────────────────────────────────────────
    # Nullable TEXT column — backward compatible, no DEFAULT required.
    # Existing rows get NULL automatically.
    op.add_column(
        "orders",
        sa.Column("cancellation_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    # A
    op.drop_column("orders", "cancellation_reason")
