"""Phase 4: Menu Localization — create 5 translation tables.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-02

Создаёт таблицы переводов для меню-сущностей:
  - category_translations
  - product_translations
  - variant_translations
  - modifier_group_translations
  - modifier_option_translations

Принципы:
  - Additive only: существующие таблицы не изменяются
  - ON DELETE CASCADE: удаление родительской сущности удаляет переводы
  - UNIQUE(entity_id, language): дубликаты запрещены на уровне БД
  - language CHECK: только 'uz', 'ru', 'en'
  - Downgrade: DROP TABLE в обратном порядке зависимостей
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: str = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. category_translations
    op.create_table(
        "category_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "category_id",
            sa.BigInteger(),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.UniqueConstraint("category_id", "language", name="uq_cat_trans_category_lang"),
        sa.CheckConstraint("language IN ('uz','ru','en')", name="ck_cat_trans_language"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_cat_trans_name_nonempty"),
    )
    op.create_index("ix_cat_trans_category_id", "category_translations", ["category_id"])

    # 2. product_translations
    op.create_table(
        "product_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("product_id", "language", name="uq_prod_trans_product_lang"),
        sa.CheckConstraint("language IN ('uz','ru','en')", name="ck_prod_trans_language"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_prod_trans_name_nonempty"),
    )
    op.create_index("ix_prod_trans_product_id", "product_translations", ["product_id"])

    # 3. variant_translations
    op.create_table(
        "variant_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "variant_id",
            sa.BigInteger(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.UniqueConstraint("variant_id", "language", name="uq_var_trans_variant_lang"),
        sa.CheckConstraint("language IN ('uz','ru','en')", name="ck_var_trans_language"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_var_trans_name_nonempty"),
    )
    op.create_index("ix_var_trans_variant_id", "variant_translations", ["variant_id"])

    # 4. modifier_group_translations
    op.create_table(
        "modifier_group_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "modifier_group_id",
            sa.BigInteger(),
            sa.ForeignKey("modifier_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.UniqueConstraint("modifier_group_id", "language", name="uq_modgrp_trans_group_lang"),
        sa.CheckConstraint("language IN ('uz','ru','en')", name="ck_modgrp_trans_language"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_modgrp_trans_name_nonempty"),
    )
    op.create_index("ix_modgrp_trans_group_id", "modifier_group_translations", ["modifier_group_id"])

    # 5. modifier_option_translations
    op.create_table(
        "modifier_option_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "modifier_option_id",
            sa.BigInteger(),
            sa.ForeignKey("modifier_options.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.UniqueConstraint("modifier_option_id", "language", name="uq_modopt_trans_option_lang"),
        sa.CheckConstraint("language IN ('uz','ru','en')", name="ck_modopt_trans_language"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_modopt_trans_name_nonempty"),
    )
    op.create_index("ix_modopt_trans_option_id", "modifier_option_translations", ["modifier_option_id"])


def downgrade() -> None:
    # Reverse order of dependencies
    op.drop_index("ix_modopt_trans_option_id", table_name="modifier_option_translations")
    op.drop_table("modifier_option_translations")

    op.drop_index("ix_modgrp_trans_group_id", table_name="modifier_group_translations")
    op.drop_table("modifier_group_translations")

    op.drop_index("ix_var_trans_variant_id", table_name="variant_translations")
    op.drop_table("variant_translations")

    op.drop_index("ix_prod_trans_product_id", table_name="product_translations")
    op.drop_table("product_translations")

    op.drop_index("ix_cat_trans_category_id", table_name="category_translations")
    op.drop_table("category_translations")
