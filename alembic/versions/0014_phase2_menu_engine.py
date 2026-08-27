"""Phase 2 — Menu Engine Foundation

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-27

S2-2: Добавляет фундамент базы данных для Menu Engine Phase 2.

Новые таблицы:
  product_variants   — варианты товара (Плов: Полная порция / Половина)
  modifier_groups    — группы модификаторов (Дополнительно)
  modifier_options   — опции модификаторов (Extra meat +10 000)

Изменения в существующих таблицах:
  order_items:
    + variant_id   (BigInteger, nullable, FK → product_variants.id SET NULL)
    + variant_name (String(255), nullable)
    Оба поля инертны до S2-5. NULL для всех заказов до активации variant order path.

НЕ изменяет:
  products.price       — остаётся NOT NULL, CHECK >= 0 (изменится в S2-5)
  products.*           — ни одна существующая колонка products не затронута
  Любые другие таблицы кроме order_items

Архитектурные решения:
  ADR-S2-1: ProductVariant прикреплён к Product (не к Location).
             Category/Product — brand-level entities (restaurant_id).
  ADR-S2-2: ModifierGroup.required отсутствует.
             Семантика: min_selections=0 → необязательно, >= 1 → обязательно.
  ADR-S2-3: Модификаторы прикреплены к Product, не к Variant.
  ADR-S2-4: Валюта не хранится на Variant/Option — наследуется из Location.currency.
  ADR-S2-5: order_items.variant_id добавлен сейчас для безопасного FK reference.
             Активируется в S2-5 одновременно с nullable Product.price.

Rollback:
  Полностью обратима. downgrade() удаляет все созданные объекты в обратном порядке.
  FK constraints именованы явно для безопасного DROP в downgrade.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: str = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── product_variants ─────────────────────────────────────────────────
    # Варианты товара. Product без вариантов → Product.price (legacy).
    # Product с вариантами → Variant.price (каждый вариант имеет свою цену).
    op.create_table(
        "product_variants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # price: цена варианта в целых сомах. 0 разрешён (бесплатный вариант).
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price >= 0", name="ck_product_variants_price_nonnegative"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_variants_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_variants_product_id", "product_variants", ["product_id"])
    op.create_index(
        "ix_variants_product_active_sort",
        "product_variants",
        ["product_id", "is_active", "sort_order"],
    )

    # ── modifier_groups ──────────────────────────────────────────────────
    # Группы модификаторов. Прикреплены к Product (не к Variant — ADR-S2-3).
    # min_selections = 0 → необязательная, >= 1 → обязательная (поля required нет).
    # max_selections = 1 → radio, > 1 → checkbox.
    op.create_table(
        "modifier_groups",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("min_selections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_selections", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("min_selections >= 0", name="ck_modifier_groups_min_selections_nonneg"),
        sa.CheckConstraint("max_selections >= 1", name="ck_modifier_groups_max_selections_positive"),
        sa.CheckConstraint(
            "max_selections >= min_selections",
            name="ck_modifier_groups_max_gte_min",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_modifier_groups_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modifier_groups_product_id", "modifier_groups", ["product_id"])
    op.create_index(
        "ix_modifier_groups_product_active",
        "modifier_groups",
        ["product_id", "is_active"],
    )

    # ── modifier_options ─────────────────────────────────────────────────
    # Опции модификаторов. price_adjustment — знаковое целое (надбавка или скидка).
    # Итоговая цена (Phase 7): variant.price + sum(selected options.price_adjustment).
    op.create_table(
        "modifier_options",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("modifier_group_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # price_adjustment: >= -1 000 000. Защита от абсурдных скидок.
        # Положительное → надбавка, отрицательное → скидка, 0 → бесплатно.
        sa.Column("price_adjustment", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_adjustment >= -1000000",
            name="ck_modifier_options_price_adjustment_range",
        ),
        sa.ForeignKeyConstraint(
            ["modifier_group_id"],
            ["modifier_groups.id"],
            name="fk_modifier_options_group_id_modifier_groups",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modifier_options_group_id", "modifier_options", ["modifier_group_id"])
    op.create_index(
        "ix_modifier_options_group_active_sort",
        "modifier_options",
        ["modifier_group_id", "is_active", "sort_order"],
    )

    # ── order_items: добавить variant_id и variant_name ──────────────────
    # Оба поля nullable. NULL для всех заказов до S2-5.
    # variant_id: FK → product_variants.id SET NULL.
    #   SET NULL гарантирует сохранность исторических OrderItem при удалении варианта.
    #   Цена уже снятшотирована в order_items.price — variant_id аналитический.
    # variant_name: snapshot имени варианта на момент заказа.
    #   Аналог order_items.name (snapshot product.name) для варианта.
    op.add_column(
        "order_items",
        sa.Column("variant_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_order_items_variant_id_product_variants",
        "order_items",
        "product_variants",
        ["variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "order_items",
        sa.Column("variant_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    # Порядок: сначала зависимые объекты, затем базовые таблицы.

    # 1. order_items.variant_name
    op.drop_column("order_items", "variant_name")

    # 2. order_items.variant_id (FK сначала, потом колонка)
    op.drop_constraint(
        "fk_order_items_variant_id_product_variants",
        "order_items",
        type_="foreignkey",
    )
    op.drop_column("order_items", "variant_id")

    # 3. modifier_options (зависит от modifier_groups)
    op.drop_index("ix_modifier_options_group_active_sort", table_name="modifier_options")
    op.drop_index("ix_modifier_options_group_id", table_name="modifier_options")
    op.drop_table("modifier_options")

    # 4. modifier_groups (зависит от products)
    op.drop_index("ix_modifier_groups_product_active", table_name="modifier_groups")
    op.drop_index("ix_modifier_groups_product_id", table_name="modifier_groups")
    op.drop_table("modifier_groups")

    # 5. product_variants (зависит от products; order_items.variant_id уже удалён)
    op.drop_index("ix_variants_product_active_sort", table_name="product_variants")
    op.drop_index("ix_variants_product_id", table_name="product_variants")
    op.drop_table("product_variants")
