"""add is_popular to products

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19

Добавляет колонку is_popular в таблицу products.

Назначение:
  - Ресторан помечает наиболее заказываемые позиции.
  - На этапе AI-2 будет заполняться автоматически из статистики.
  - Используется на главном экране Mini App в горизонтальном скролле.

Для существующих БД:
  alembic upgrade 0003
  — или вручную: ALTER TABLE products ADD COLUMN IF NOT EXISTS is_popular BOOLEAN NOT NULL DEFAULT false;

Индекс ix_products_popular:
  Partial index (WHERE is_popular = TRUE) — по аналогии с ix_products_bestseller.
  Если миграция уже применялась, выполни вручную в Neon SQL Editor:
    DROP INDEX IF EXISTS ix_products_popular;
    CREATE INDEX IF NOT EXISTS ix_products_popular
      ON products (restaurant_id) WHERE is_popular = TRUE;
"""


from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_popular",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Partial index: индексирует только строки WHERE is_popular = TRUE.
    # Аналогично ix_products_bestseller в миграции 0002.
    # Меньше размером, быстрее обновляется, эффективнее для
    # запросов вида WHERE is_popular = TRUE (горизонтальный скролл Mini App).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_popular "
        "ON products (restaurant_id) "
        "WHERE is_popular = TRUE"
    )


def downgrade() -> None:
    op.drop_index("ix_products_popular", table_name="products")
    op.drop_column("products", "is_popular")
