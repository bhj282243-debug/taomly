"""S2-5 — Product.price nullable для Variant Engine

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-28

S2-5: Атомарная активация variant order path.

Изменения:
  products.price:
    - Убирает NOT NULL constraint → nullable
    - Убирает старый CHECK ck_products_price_nonnegative (несовместим с nullable в PostgreSQL)
    - Добавляет новый CHECK ck_products_price_nonneg_or_null: price IS NULL OR price >= 0
      (NULL разрешён для variant-продуктов, 0+ для legacy-продуктов)

Инварианты после миграции:
  - Все существующие Product.price значения сохраняются без изменений
  - Legacy products (price IS NOT NULL) продолжают работать через CASE A order path
  - Variant products (price IS NULL) используют CASE B/C order path
  - Существующие OrderItem.price snapshot'ы не затронуты

Rollback:
  downgrade() безопасен если нет продуктов с price IS NULL.
  Если есть — downgrade невозможен (NOT NULL constraint rejection).
  Рекомендуется выполнять rollback только при отсутствии variant-продуктов.

Цепочка: 0013 → 0014 → 0015
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: str = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Шаг 1: удалить старый CHECK constraint ────────────────────────────
    # ck_products_price_nonnegative требует price >= 0, что несовместимо с NULL.
    # PostgreSQL: CHECK constraint с NULL → результат UNKNOWN → строка проходит,
    # но для явности и чистоты заменяем на новый constraint с IS NULL OR price >= 0.
    op.drop_constraint(
        "ck_products_price_nonnegative",
        "products",
        type_="check",
    )

    # ── Шаг 2: сделать products.price nullable ────────────────────────────
    # existing=True сохраняет все существующие значения.
    op.alter_column(
        "products",
        "price",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # ── Шаг 3: добавить новый CHECK constraint ────────────────────────────
    # Разрешает NULL (variant-продукты) и price >= 0 (legacy-продукты).
    # Отрицательная цена недопустима в обоих случаях.
    op.create_check_constraint(
        "ck_products_price_nonneg_or_null",
        "products",
        "price IS NULL OR price >= 0",
    )


def downgrade() -> None:
    # ── Шаг 1: удалить новый CHECK constraint ────────────────────────────
    op.drop_constraint(
        "ck_products_price_nonneg_or_null",
        "products",
        type_="check",
    )

    # ── Шаг 2: вернуть NOT NULL ───────────────────────────────────────────
    # ВНИМАНИЕ: завершится ошибкой если в таблице есть продукты с price IS NULL.
    # Перед rollback необходимо убедиться что таких продуктов нет.
    op.alter_column(
        "products",
        "price",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # ── Шаг 3: восстановить оригинальный CHECK constraint ─────────────────
    op.create_check_constraint(
        "ck_products_price_nonnegative",
        "products",
        "price >= 0",
    )
