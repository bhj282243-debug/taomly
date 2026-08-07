"""add revoked_tokens, subscription_plans, subscriptions, usage_events

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07

Создаёт четыре таблицы, отсутствующие в предыдущих Alembic-миграциях:

  revoked_tokens      — JWT revocation list (logout, token invalidation).
  subscription_plans  — тарифные планы платформы.
  subscriptions       — подписки ресторанов на тарифные планы.
  usage_events        — события использования квот (orders, products).

Также выполняет seed: создаёт стартовые тарифные планы
Free / Basic / Pro, если они ещё не существуют (ON CONFLICT DO NOTHING).

Совместимость с существующими БД:
  Если таблицы уже были созданы вручную через MIGRATION_billing.sql или
  иным образом — миграция завершится ошибкой при попытке создать
  существующую таблицу. В этом случае выполни в Neon SQL Editor:

    SELECT alembic_stamp('0005');

  — это пометит миграцию как выполненную без её применения.
  Подробнее: DEPLOYMENT.md → «Existing database migration path».
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ──────────────────────────────────────────
# UPGRADE
# ──────────────────────────────────────────

def upgrade() -> None:
    # ── 1. revoked_tokens ─────────────────────────────────────────────────────
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("jti", sa.String(36), nullable=False),
        sa.Column(
            "token_type",
            sa.String(16),
            server_default="access",
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
    )
    op.create_index(
        "ix_revoked_tokens_expires_at",
        "revoked_tokens",
        ["expires_at"],
    )

    # ── 2. subscription_plans ─────────────────────────────────────────────────
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("price", sa.Integer(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(10), server_default="USD", nullable=False),
        sa.Column("orders_per_month", sa.Integer(), server_default="100", nullable=False),
        sa.Column("products_limit", sa.Integer(), server_default="20", nullable=False),
        sa.Column("users_limit", sa.Integer(), server_default="-1", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("price >= 0", name="ck_subscription_plans_price_nonnegative"),
    )

    # ── 3. subscriptions ──────────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscriptions_restaurant_active",
        "subscriptions",
        ["restaurant_id", "is_active"],
    )
    op.create_index(
        "ix_subscriptions_restaurant_id",
        "subscriptions",
        ["restaurant_id"],
    )

    # ── 4. usage_events ───────────────────────────────────────────────────────
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "event_type IN ('order_created','product_created','product_deleted')",
            name="check_usage_event_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_events_restaurant_month",
        "usage_events",
        ["restaurant_id", "created_at"],
    )
    op.create_index(
        "ix_usage_events_restaurant_id",
        "usage_events",
        ["restaurant_id"],
    )

    # ── 5. Seed: стартовые тарифные планы ─────────────────────────────────────
    # Используем pg_insert с ON CONFLICT DO NOTHING — идемпотентно.
    # Если планы уже существуют (например, из MIGRATION_billing.sql),
    # INSERT молча пропустит их без ошибок.
    conn = op.get_bind()
    plans_table = sa.table(
        "subscription_plans",
        sa.column("name", sa.String),
        sa.column("price", sa.Integer),
        sa.column("currency", sa.String),
        sa.column("orders_per_month", sa.Integer),
        sa.column("products_limit", sa.Integer),
        sa.column("users_limit", sa.Integer),
        sa.column("description", sa.Text),
        sa.column("is_active", sa.Boolean),
    )
    conn.execute(
        pg_insert(plans_table).values(
            [
                {
                    "name": "Free",
                    "price": 0,
                    "currency": "USD",
                    "orders_per_month": 100,
                    "products_limit": 20,
                    "users_limit": -1,
                    "description": "Бесплатный тариф — до 100 заказов и 20 блюд в месяц",
                    "is_active": True,
                },
                {
                    "name": "Basic",
                    "price": 29,
                    "currency": "USD",
                    "orders_per_month": 500,
                    "products_limit": 100,
                    "users_limit": -1,
                    "description": "Базовый тариф — до 500 заказов и 100 блюд в месяц",
                    "is_active": True,
                },
                {
                    "name": "Pro",
                    "price": 79,
                    "currency": "USD",
                    "orders_per_month": 2000,
                    "products_limit": 500,
                    "users_limit": -1,
                    "description": "Профессиональный — до 2000 заказов и 500 блюд в месяц",
                    "is_active": True,
                },
            ]
        ).on_conflict_do_nothing(index_elements=["name"])
    )


# ──────────────────────────────────────────
# DOWNGRADE
# ──────────────────────────────────────────

def downgrade() -> None:
    # Удаляем в обратном порядке — сначала таблицы с FK.
    op.drop_index("ix_usage_events_restaurant_id", table_name="usage_events")
    op.drop_index("ix_usage_events_restaurant_month", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index("ix_subscriptions_restaurant_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_restaurant_active", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_table("subscription_plans")

    op.drop_index("ix_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
