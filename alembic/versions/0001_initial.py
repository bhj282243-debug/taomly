"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-05 00:00:00

Первая миграция — создаёт все таблицы из текущих моделей.
Включает badge-колонки в products (is_bestseller, is_new, is_spicy, is_chef_choice).

Для существующих БД (созданных через create_all):
  1. Запустить MIGRATION_badges.sql в Neon SQL Editor (если ещё не сделано)
  2. Затем выполнить: alembic stamp 0001_initial
     (помечает текущую схему как соответствующую этой ревизии без применения)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── agencies ────────────────────────────────────────────────
    op.create_table(
        "agencies",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column("owner_password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agencies_owner_email", "agencies", ["owner_email"], unique=True)

    # ── restaurants ─────────────────────────────────────────────
    op.create_table(
        "restaurants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("agency_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_waiter_call_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("admin_password_hash", sa.String(255), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("primary_color", sa.String(20), nullable=False, server_default="#8B1A2E"),
        sa.Column("secondary_color", sa.String(20), nullable=False, server_default="#FAF6EE"),
        sa.Column("accent_color", sa.String(20), nullable=False, server_default="#D4A853"),
        sa.Column("welcome_text", sa.Text(), nullable=True),
        sa.Column("custom_domain", sa.String(255), nullable=True),
        sa.Column("telegram_bot_token_encrypted", sa.Text(), nullable=True),
        sa.Column("telegram_dispatcher_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("custom_domain"),
    )
    op.create_index("ix_restaurants_slug", "restaurants", ["slug"], unique=True)
    op.create_index("ix_restaurants_agency_active", "restaurants", ["agency_id", "is_active"])

    # ── users ────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="client"),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('admin','owner','dispatcher','client')", name="check_user_role"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("restaurant_id", "telegram_id", name="uq_user_restaurant_telegram"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])
    op.create_index("ix_users_restaurant_role", "users", ["restaurant_id", "role"])

    # ── categories ───────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("restaurant_id", "name", name="uq_category_restaurant_name"),
    )
    op.create_index("ix_categories_restaurant_sort", "categories", ["restaurant_id", "sort_order"])

    # ── products ─────────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_bestseller", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_spicy", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_chef_choice", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_restaurant_available_sort", "products", ["restaurant_id", "is_available", "sort_order"])
    op.create_index("ix_products_category", "products", ["category_id"])
    op.create_index("ix_products_bestseller", "products", ["restaurant_id", "is_bestseller"], postgresql_where=sa.text("is_bestseller = TRUE"))

    # ── restaurant_tables ────────────────────────────────────────
    op.create_table(
        "restaurant_tables",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("table_number", sa.String(50), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("restaurant_id", "table_number", name="uq_table_restaurant_number"),
    )

    # ── orders ───────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=True),
        sa.Column("client_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column("client_phone", sa.String(50), nullable=True),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("location_lat", sa.Float(), nullable=True),
        sa.Column("location_lng", sa.Float(), nullable=True),
        sa.Column("table_id", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("order_type IN ('delivery','takeaway','dine_in')", name="check_order_type"),
        sa.CheckConstraint("status IN ('new','accepted','preparing','ready_for_delivery','delivering','completed','cancelled')", name="check_order_status"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["table_id"], ["restaurant_tables.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_restaurant_status_created", "orders", ["restaurant_id", "status", "created_at"])
    op.create_index("ix_orders_client_telegram", "orders", ["client_telegram_id"])

    # ── order_items ──────────────────────────────────────────────
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="check_order_item_quantity"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_items_order", "order_items", ["order_id"])

    # ── reservations ─────────────────────────────────────────────
    op.create_table(
        "reservations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("client_phone", sa.String(50), nullable=False),
        sa.Column("guests_count", sa.Integer(), nullable=False),
        sa.Column("reservation_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('new','confirmed','completed','cancelled')", name="check_reservation_status"),
        sa.CheckConstraint("guests_count > 0", name="check_reservation_guests"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reservations_restaurant_time", "reservations", ["restaurant_id", "reservation_time"])

    # ── waiter_calls ─────────────────────────────────────────────
    op.create_table(
        "waiter_calls",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active','accepted','completed','cancelled')", name="check_waiter_call_status"),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["restaurant_tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_waiter_calls_restaurant_status", "waiter_calls", ["restaurant_id", "status"])


def downgrade() -> None:
    op.drop_table("waiter_calls")
    op.drop_table("reservations")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("restaurant_tables")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("users")
    op.drop_table("restaurants")
    op.drop_table("agencies")
