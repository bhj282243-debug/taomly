"""Phase 3 — Menu Availability + Scheduling

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-30

Phase 3: Menu Availability + Scheduling.

Новые поля:

  products:
    available_from  TIME NULL  — начало окна доступности (NULL = без расписания)
    available_until TIME NULL  — конец окна доступности  (NULL = без расписания)

    Правила:
      NULL / NULL              → нет расписания, доступность определяется только is_available
      from < until             → нормальное окно (11:00–22:00)
      from > until             → overnight (22:00–02:00)
      from == until            → 24 часа / всегда доступно

  product_variants:
    is_available  BOOLEAN NOT NULL DEFAULT TRUE
      Семантика отличается от is_active:
        is_active=false   → вариант скрыт полностью (admin отключил)
        is_available=false → вариант виден, но disabled ("Sold out", временно)

  modifier_options:
    is_available  BOOLEAN NOT NULL DEFAULT TRUE
      Аналогично product_variants.is_available.
      ModifierGroup.is_available НЕ добавляется — группы управляются только через is_active.

Backward compatibility:
  - Все существующие продукты: available_from=NULL, available_until=NULL → без расписания
  - Все существующие варианты: is_available=TRUE → доступны (как прежде)
  - Все существующие опции:    is_available=TRUE → доступны (как прежде)
  - Ни одна существующая запись не меняет своё observable поведение

Индексы:
  ix_variants_product_available — составной для hot path фильтрации вариантов
  ix_modifier_options_group_available — аналогично для опций

Цепочка: 0014 → 0015 → 0016 → 0017
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: str = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── products: поля расписания ──────────────────────────────────────────
    # TIME тип PostgreSQL: хранит время суток без даты и без timezone.
    # NULL = расписание не задано (продукт доступен согласно только is_available).
    op.add_column(
        "products",
        sa.Column("available_from", sa.Time(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("available_until", sa.Time(), nullable=True),
    )

    # ── product_variants: is_available ────────────────────────────────────
    # DEFAULT TRUE: все существующие варианты остаются доступными.
    op.add_column(
        "product_variants",
        sa.Column(
            "is_available",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    # Индекс для hot path: фильтрация active+available вариантов продукта.
    op.create_index(
        "ix_variants_product_available",
        "product_variants",
        ["product_id", "is_active", "is_available", "sort_order"],
    )

    # ── modifier_options: is_available ────────────────────────────────────
    # DEFAULT TRUE: все существующие опции остаются доступными.
    op.add_column(
        "modifier_options",
        sa.Column(
            "is_available",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    # Индекс для hot path: фильтрация active+available опций группы.
    op.create_index(
        "ix_modifier_options_group_available",
        "modifier_options",
        ["modifier_group_id", "is_active", "is_available", "sort_order"],
    )


def downgrade() -> None:
    # Откат в обратном порядке.
    op.drop_index("ix_modifier_options_group_available", table_name="modifier_options")
    op.drop_column("modifier_options", "is_available")

    op.drop_index("ix_variants_product_available", table_name="product_variants")
    op.drop_column("product_variants", "is_available")

    op.drop_column("products", "available_until")
    op.drop_column("products", "available_from")
