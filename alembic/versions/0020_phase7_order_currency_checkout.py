"""
Phase 7: Order Engine — orders.currency + carts.checkout_idempotency_key

Revision ID: 0020
Revises: 0019

A. orders.currency — immutable snapshot валюты заказа.
   Flow: Location.currency → Cart.currency → Order.currency
   Порядок: ADD NULL → BACKFILL → VERIFY → NOT NULL → CHECK

B. carts.checkout_idempotency_key — opaque idempotency key.
   VARCHAR(64) NULLABLE, partial UNIQUE INDEX.
"""

from __future__ import annotations
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── A. orders.currency ─────────────────────────────────────────
    # Step 1: nullable column
    op.add_column("orders", sa.Column("currency", sa.String(10), nullable=True))

    # Step 2: backfill from location
    op.execute(sa.text("""
        UPDATE orders o
        SET currency = (
            SELECT l.currency FROM locations l WHERE l.id = o.location_id
        )
        WHERE o.currency IS NULL
    """))

    # Step 3: verify — halt if any NULL remains
    bind = op.get_bind()
    null_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM orders WHERE currency IS NULL")
    ).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"Migration 0020 HALTED: {null_count} order(s) have NULL currency. "
            "Fix data integrity before re-running."
        )

    # Step 4: NOT NULL
    op.alter_column("orders", "currency", nullable=False)

    # Step 5: CHECK constraint
    op.create_check_constraint(
        "ck_orders_currency", "orders",
        "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
    )

    # ── B. carts.checkout_idempotency_key ──────────────────────────
    op.add_column("carts", sa.Column(
        "checkout_idempotency_key", sa.String(64), nullable=True
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_carts_checkout_idempotency_key "
        "ON carts(checkout_idempotency_key) "
        "WHERE checkout_idempotency_key IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_carts_checkout_idempotency_key"))
    op.drop_column("carts", "checkout_idempotency_key")
    op.drop_constraint("ck_orders_currency", "orders", type_="check")
    op.drop_column("orders", "currency")
