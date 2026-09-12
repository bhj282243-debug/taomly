"""
Phase 8: Payment Engine

Revision ID: 0021
Revises: 0020

Creates:
  A. restaurant_payment_configs — per-restaurant merchant credentials
  B. payments                   — customer payment entity (source of truth)
  C. payment_attempts           — provider interaction audit trail
  D. orders.paid_at             — projection fact (nullable timestamp)

Money convention: integer tiyins throughout (same as existing Order.total_amount).
Click float soums are converted to tiyins in the adapter layer, not here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── A. restaurant_payment_configs ─────────────────────────────────────────
    op.create_table(
        "restaurant_payment_configs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "restaurant_id",
            sa.BigInteger,
            sa.ForeignKey("restaurants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        # Payme: Basic Auth username + callback routing + checkout URL
        # Click: checkout URL only (NOT used for callback routing)
        sa.Column("merchant_id", sa.String(255), nullable=False),
        # Click only: callback routing + sign_string component. NULL for Payme.
        sa.Column("service_id", sa.String(100), nullable=True),
        # Fernet-encrypted merchant secret. Never expose raw.
        sa.Column("encrypted_secret", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('payme', 'click')",
            name="ck_rpc_provider",
        ),
    )
    # One config per provider per restaurant
    op.create_index(
        "uq_rpc_restaurant_provider",
        "restaurant_payment_configs",
        ["restaurant_id", "provider"],
        unique=True,
    )
    op.create_index(
        "ix_rpc_restaurant_id",
        "restaurant_payment_configs",
        ["restaurant_id"],
    )
    # Payme callback lookup by merchant_id
    op.create_index(
        "ix_rpc_merchant_provider",
        "restaurant_payment_configs",
        ["merchant_id", "provider"],
    )
    # Click callback lookup by service_id
    op.create_index(
        "ix_rpc_service_provider",
        "restaurant_payment_configs",
        ["service_id", "provider"],
    )

    # ── B. payments ───────────────────────────────────────────────────────────
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger,
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "restaurant_id",
            sa.BigInteger,
            sa.ForeignKey("restaurants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        # Integer tiyins — sourced from Order.total_amount, never from client.
        sa.Column("amount", sa.Integer, nullable=False),
        # Sourced from Order.currency, never from client.
        sa.Column("currency", sa.String(10), nullable=False),
        # 'payme' | 'click' | 'sandbox'
        sa.Column("provider", sa.String(50), nullable=False),
        # Provider's own transaction identifier (set after provider interaction)
        sa.Column("provider_transaction_id", sa.String(255), nullable=True),
        # Client idempotency key (opaque, up to 64 chars)
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        # Set when Payment transitions to PAID
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','processing','paid','failed','cancelled')",
            name="ck_payments_status",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_payments_amount_nonneg",
        ),
        sa.CheckConstraint(
            "currency IN ('UZS','KZT','RUB','USD','TRY','AED')",
            name="ck_payments_currency",
        ),
        sa.CheckConstraint(
            "provider IN ('payme','click','sandbox')",
            name="ck_payments_provider",
        ),
    )

    # Idempotency: same (order_id, idempotency_key) → same Payment
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_payments_order_idempotency "
        "ON payments(order_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    ))

    # Active invariant: at most one PENDING or PROCESSING Payment per Order
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_payments_order_active "
        "ON payments(order_id) "
        "WHERE status IN ('pending', 'processing')"
    ))

    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_index("ix_payments_restaurant_id", "payments", ["restaurant_id"])
    # Fast lookup for provider callback by transaction ID
    op.execute(sa.text(
        "CREATE INDEX ix_payments_provider_transaction "
        "ON payments(provider, provider_transaction_id) "
        "WHERE provider_transaction_id IS NOT NULL"
    ))

    # ── C. payment_attempts ───────────────────────────────────────────────────
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "payment_id",
            sa.BigInteger,
            sa.ForeignKey("payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_transaction_id", sa.String(255), nullable=True),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','success','failed','cancelled')",
            name="ck_pa_status",
        ),
    )
    op.create_index(
        "ix_payment_attempts_payment_id",
        "payment_attempts",
        ["payment_id"],
    )

    # ── D. orders.paid_at ─────────────────────────────────────────────────────
    # Projection fact: written by Payment Service when Payment → PAID.
    # NOT a second source of truth. Payment.status remains authoritative.
    op.add_column(
        "orders",
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # D
    op.drop_column("orders", "paid_at")
    # C
    op.drop_index("ix_payment_attempts_payment_id", table_name="payment_attempts")
    op.drop_table("payment_attempts")
    # B
    op.execute(sa.text("DROP INDEX IF EXISTS ix_payments_provider_transaction"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_payments_order_active"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_payments_order_idempotency"))
    op.drop_index("ix_payments_restaurant_id", table_name="payments")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_table("payments")
    # A
    op.drop_index("ix_rpc_service_provider", table_name="restaurant_payment_configs")
    op.drop_index("ix_rpc_merchant_provider", table_name="restaurant_payment_configs")
    op.drop_index("ix_rpc_restaurant_id", table_name="restaurant_payment_configs")
    op.drop_index("uq_rpc_restaurant_provider", table_name="restaurant_payment_configs")
    op.drop_table("restaurant_payment_configs")
