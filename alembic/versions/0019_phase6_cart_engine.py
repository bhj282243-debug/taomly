"""Phase 6: Cart Engine — carts, cart_items, cart_item_modifiers

Revision ID: 0019
Revises: 0018
Create Date: 2025-01-01

New tables:
  carts              — one active cart per (session_id, restaurant_id)
  cart_items         — line items with server-authoritative price snapshot
  cart_item_modifiers — modifier option snapshots per cart item

Key constraint:
  uq_cart_item_identity — functional unique index on cart_items:
      UNIQUE (cart_id, product_id, COALESCE(variant_id, 0), modifiers_hash)
  This enables atomic INSERT ... ON CONFLICT DO UPDATE for concurrent adds.
  Named constraint allows SQLAlchemy's on_conflict_do_update(constraint=...) syntax.

No existing tables are modified.
Rollback: DROP TABLE cascade in reverse order.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0019"
down_revision: str = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── carts ────────────────────────────────────────────────────────────────
    op.create_table(
        "carts",
        sa.Column("id",            sa.BigInteger(), primary_key=True),
        sa.Column("restaurant_id", sa.BigInteger(), sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id",    sa.String(64),   nullable=False),
        sa.Column("telegram_id",   sa.BigInteger(), nullable=True),
        sa.Column("currency",      sa.String(10),   nullable=False, server_default="UZS"),
        sa.Column("status",        sa.String(20),   nullable=False, server_default="active"),
        sa.Column("created_at",    sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",    sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'checked_out', 'abandoned')",
            name="ck_carts_status",
        ),
        sa.CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_carts_currency",
        ),
    )
    op.create_index("ix_carts_restaurant_id",     "carts", ["restaurant_id"])
    op.create_index("ix_carts_session_restaurant", "carts", ["session_id", "restaurant_id"], unique=True)
    # Partial index for telegram_id lookup (non-null only)
    op.execute(
        "CREATE INDEX ix_carts_telegram_restaurant ON carts (telegram_id, restaurant_id) "
        "WHERE telegram_id IS NOT NULL"
    )

    # ── cart_items ────────────────────────────────────────────────────────────
    op.create_table(
        "cart_items",
        sa.Column("id",             sa.BigInteger(), primary_key=True),
        sa.Column("cart_id",        sa.BigInteger(), sa.ForeignKey("carts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id",     sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id",     sa.BigInteger(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("quantity",       sa.Integer(),    nullable=False),
        sa.Column("unit_price",     sa.Integer(),    nullable=False),
        sa.Column("modifiers_hash", sa.String(64),   nullable=False),
        sa.Column("notes",          sa.Text(),       nullable=True),
        sa.Column("line_total",     sa.Integer(),    nullable=False),
        sa.Column("added_at",       sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0",    name="ck_cart_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_cart_items_unit_price_nonneg"),
        sa.CheckConstraint("line_total >= 0", name="ck_cart_items_line_total_nonneg"),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])

    # Functional unique index — CartItem identity.
    # COALESCE(variant_id, 0) handles nullable variant_id:
    # PostgreSQL treats NULL != NULL in standard UNIQUE, so without COALESCE
    # two items with variant_id=NULL would not conflict. Using 0 as sentinel
    # is safe because product_variants.id is BIGSERIAL starting at 1.
    #
    # Named "uq_cart_item_identity" so SQLAlchemy can reference it:
    #   pg_insert(CartItem).on_conflict_do_update(constraint="uq_cart_item_identity", ...)
    op.execute(
        "CREATE UNIQUE INDEX uq_cart_item_identity ON cart_items "
        "(cart_id, product_id, COALESCE(variant_id, 0), modifiers_hash)"
    )

    # ── cart_item_modifiers ───────────────────────────────────────────────────
    op.create_table(
        "cart_item_modifiers",
        sa.Column("id",                 sa.BigInteger(), primary_key=True),
        sa.Column("cart_item_id",       sa.BigInteger(), sa.ForeignKey("cart_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("modifier_option_id", sa.BigInteger(), sa.ForeignKey("modifier_options.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name",               sa.String(255),  nullable=False),
        sa.Column("price_adjustment",   sa.Integer(),    nullable=False),
    )
    op.create_index("ix_cart_item_modifiers_item_id", "cart_item_modifiers", ["cart_item_id"])


def downgrade() -> None:
    op.drop_index("ix_cart_item_modifiers_item_id", table_name="cart_item_modifiers")
    op.drop_table("cart_item_modifiers")

    op.execute("DROP INDEX IF EXISTS uq_cart_item_identity")
    op.drop_index("ix_cart_items_cart_id", table_name="cart_items")
    op.drop_table("cart_items")

    op.execute("DROP INDEX IF EXISTS ix_carts_telegram_restaurant")
    op.drop_index("ix_carts_session_restaurant", table_name="carts")
    op.drop_index("ix_carts_restaurant_id",      table_name="carts")
    op.drop_table("carts")
