"""
modules/cart/models.py — Taomly Platform
Phase 6: Cart Engine ORM models.
Phase 7: Added Cart.checkout_idempotency_key.
"""

from sqlalchemy import (
    BigInteger, Column, CheckConstraint,
    ForeignKey, Index, Integer, String, Text, TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class Cart(Base):
    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'checked_out', 'abandoned')",
            name="ck_carts_status",
        ),
        CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_carts_currency",
        ),
        Index("ix_carts_session_restaurant", "session_id", "restaurant_id", unique=True),
        Index(
            "ix_carts_telegram_restaurant",
            "telegram_id", "restaurant_id",
            postgresql_where="telegram_id IS NOT NULL",
        ),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger, ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    session_id  = Column(String(64), nullable=False)
    telegram_id = Column(BigInteger, nullable=True)
    currency    = Column(String(10), nullable=False)
    status      = Column(String(20), nullable=False, default="active", server_default="active")
    # Phase 7: idempotency key supplied by client at checkout.
    # NULL = checkout without key (or not yet checked out).
    # Set atomically with status='checked_out' in checkout transaction.
    checkout_idempotency_key = Column(String(64), nullable=True)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    restaurant = relationship("Restaurant", lazy="select")
    items      = relationship(
        "CartItem", back_populates="cart",
        cascade="all, delete-orphan", lazy="select",
        order_by="CartItem.id",
    )

    def __repr__(self) -> str:
        return (
            f"<Cart id={self.id} restaurant_id={self.restaurant_id} "
            f"session={self.session_id!r} status={self.status!r}>"
        )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_cart_items_unit_price_nonneg"),
        CheckConstraint("line_total >= 0", name="ck_cart_items_line_total_nonneg"),
        Index("ix_cart_items_cart_id", "cart_id"),
    )

    id             = Column(BigInteger, primary_key=True)
    cart_id        = Column(
        BigInteger, ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False, index=False,
    )
    product_id     = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
    )
    variant_id     = Column(
        BigInteger, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True,
    )
    quantity       = Column(Integer, nullable=False)
    unit_price     = Column(Integer, nullable=False)
    modifiers_hash = Column(String(64), nullable=False)
    notes          = Column(Text, nullable=True)
    line_total     = Column(Integer, nullable=False)
    added_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at     = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    cart    = relationship("Cart", back_populates="items", lazy="select")
    product = relationship("Product", lazy="select")
    variant = relationship("ProductVariant", lazy="select")
    modifiers = relationship(
        "CartItemModifier", back_populates="cart_item",
        cascade="all, delete-orphan", lazy="select",
        order_by="CartItemModifier.id",
    )

    def __repr__(self) -> str:
        return (
            f"<CartItem id={self.id} cart_id={self.cart_id} "
            f"product_id={self.product_id} qty={self.quantity} unit_price={self.unit_price}>"
        )


class CartItemModifier(Base):
    __tablename__ = "cart_item_modifiers"
    __table_args__ = (
        Index("ix_cart_item_modifiers_item_id", "cart_item_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    cart_item_id       = Column(
        BigInteger, ForeignKey("cart_items.id", ondelete="CASCADE"),
        nullable=False, index=False,
    )
    modifier_option_id = Column(
        BigInteger, ForeignKey("modifier_options.id", ondelete="SET NULL"), nullable=True,
    )
    name             = Column(String(255), nullable=False)
    price_adjustment = Column(Integer, nullable=False)

    cart_item = relationship("CartItem", back_populates="modifiers", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<CartItemModifier id={self.id} cart_item_id={self.cart_item_id} "
            f"name={self.name!r} adj={self.price_adjustment}>"
        )
