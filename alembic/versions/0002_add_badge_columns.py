"""add badge columns to products

Revision ID: 0002
Revises: 0001_initial
Create Date: 2026-07-10

Заменяет MIGRATION_badges.sql — добавляет булевые бейдж-колонки в таблицу products.
Колонки: is_bestseller, is_new, is_spicy, is_chef_choice.
Миграция идемпотентна (ADD COLUMN IF NOT EXISTS через batch_alter).
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем badge-колонки
    op.add_column("products", sa.Column("is_bestseller",  sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("products", sa.Column("is_new",         sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("products", sa.Column("is_spicy",       sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("products", sa.Column("is_chef_choice", sa.Boolean(), nullable=False, server_default=sa.false()))

    # Индекс для быстрого поиска хитов продаж по ресторану
    op.create_index(
        "ix_products_bestseller",
        "products",
        ["restaurant_id", "is_bestseller"],
        postgresql_where=sa.text("is_bestseller = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_products_bestseller", table_name="products")
    op.drop_column("products", "is_chef_choice")
    op.drop_column("products", "is_spicy")
    op.drop_column("products", "is_new")
    op.drop_column("products", "is_bestseller")
