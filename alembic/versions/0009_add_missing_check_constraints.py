"""add missing CHECK constraints and FK indexes

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-16

Добавляет CHECK constraints, которые присутствуют в SQLAlchemy-моделях,
но отсутствовали в migration 0001_initial.py:

  ck_orders_total_amount_nonnegative   — orders.total_amount >= 0
  ck_order_items_price_nonnegative     — order_items.price >= 0
  ck_products_price_nonnegative        — products.price >= 0

Также добавляет недостающие индексы на FK-колонки:
  ix_orders_client_id       — orders.client_id (FK → users.id)
  ix_order_items_product_id — order_items.product_id (FK → products.id)

Без индексов на FK PostgreSQL выполняет SeqScan при каскадных операциях
и JOIN-запросах (Order → User, OrderItem → Product).

Все операции идемпотентны (IF NOT EXISTS / DO $$ ... $$).
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── CHECK constraints для денежных полей ──────────────────────────────────
    # PostgreSQL не поддерживает CREATE CONSTRAINT IF NOT EXISTS,
    # поэтому используем DO $$ ... $$ с проверкой через pg_constraint.

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_orders_total_amount_nonnegative'
                  AND conrelid = 'orders'::regclass
            ) THEN
                ALTER TABLE orders
                    ADD CONSTRAINT ck_orders_total_amount_nonnegative
                    CHECK (total_amount >= 0);
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_order_items_price_nonnegative'
                  AND conrelid = 'order_items'::regclass
            ) THEN
                ALTER TABLE order_items
                    ADD CONSTRAINT ck_order_items_price_nonnegative
                    CHECK (price >= 0);
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_products_price_nonnegative'
                  AND conrelid = 'products'::regclass
            ) THEN
                ALTER TABLE products
                    ADD CONSTRAINT ck_products_price_nonnegative
                    CHECK (price >= 0);
            END IF;
        END
        $$;
    """)

    # ── Индексы на FK-колонки без индексов ────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_client_id ON orders (client_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_order_items_product_id ON order_items (product_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_order_items_product_id")
    op.execute("DROP INDEX IF EXISTS ix_orders_client_id")

    op.drop_constraint(
        "ck_products_price_nonnegative", "products", type_="check"
    )
    op.drop_constraint(
        "ck_order_items_price_nonnegative", "order_items", type_="check"
    )
    op.drop_constraint(
        "ck_orders_total_amount_nonnegative", "orders", type_="check"
    )
