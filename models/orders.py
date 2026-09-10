"""
models/orders.py — Taomly Platform
Phase 7: Added Order.currency — immutable snapshot of Location.currency at checkout.
Flow: Location.currency → Cart.currency → Order.currency
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, CheckConstraint, Float,
    ForeignKey, Index, Integer, String, Text, TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "order_type IN ('delivery','takeaway','dine_in')",
            name="check_order_type",
        ),
        CheckConstraint(
            "status IN ('new','accepted','preparing','ready_for_delivery','delivering','completed','cancelled')",
            name="check_order_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_orders_total_amount_nonnegative"),
        # Phase 7: currency immutable snapshot from Cart.currency at checkout.
        CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_orders_currency",
        ),
        Index("ix_orders_restaurant_status_created", "restaurant_id", "status", "created_at"),
        Index("ix_orders_client_telegram", "client_telegram_id"),
        Index("ix_orders_location_id", "location_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    restaurant_id      = Column(
        BigInteger, ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    location_id        = Column(
        BigInteger, ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_id          = Column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    client_telegram_id = Column(BigInteger, nullable=True)
    client_name        = Column(String(255))
    client_phone       = Column(String(50))
    order_type         = Column(String(20), nullable=False)
    address            = Column(Text)
    location_lat       = Column(Float)
    location_lng       = Column(Float)
    table_id           = Column(
        BigInteger, ForeignKey("restaurant_tables.id", ondelete="SET NULL"), nullable=True,
    )
    comment      = Column(Text)
    total_amount = Column(Integer, nullable=False)
    # Phase 7: immutable currency snapshot — set once at checkout from Cart.currency.
    # Never updated after Order creation.
    currency     = Column(String(10), nullable=False)
    status       = Column(String(20), default="new", nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at   = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    restaurant = relationship("Restaurant", back_populates="orders", lazy="select")
    location   = relationship("Location", lazy="select")
    client     = relationship("User", lazy="select")
    items      = relationship(
        "OrderItem", back_populates="order",
        cascade="all, delete-orphan", lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status!r} total={self.total_amount}>"


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_order_item_quantity"),
        CheckConstraint("price >= 0", name="ck_order_items_price_nonnegative"),
    )

    id         = Column(BigInteger, primary_key=True)
    order_id   = Column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
    )
    name         = Column(String(255), nullable=False)
    price        = Column(Integer, nullable=False)
    quantity     = Column(Integer, nullable=False)
    variant_id   = Column(
        BigInteger, ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True,
    )
    variant_name = Column(String(255), nullable=True)

    order   = relationship("Order", back_populates="items", lazy="select")
    product = relationship("Product", lazy="select")
    variant = relationship("ProductVariant", lazy="select")
    selected_modifiers = relationship(
        "OrderItemModifier", back_populates="order_item",
        cascade="all, delete-orphan", lazy="select",
    )

    def __repr__(self) -> str:
        return f"<OrderItem id={self.id} name={self.name!r} qty={self.quantity}>"


class OrderItemModifier(Base):
    __tablename__ = "order_item_modifiers"
    __table_args__ = (
        Index("ix_order_item_modifiers_order_item_id", "order_item_id"),
        Index("ix_order_item_modifiers_option_id", "modifier_option_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    order_item_id      = Column(
        BigInteger, ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=False, index=False,
    )
    modifier_option_id = Column(
        BigInteger, ForeignKey("modifier_options.id", ondelete="SET NULL"),
        nullable=True, index=False,
    )
    name             = Column(String(255), nullable=False)
    price_adjustment = Column(Integer, nullable=False)

    order_item = relationship("OrderItem", back_populates="selected_modifiers")

    def __repr__(self) -> str:
        return (
            f"<OrderItemModifier id={self.id} "
            f"order_item_id={self.order_item_id} name={self.name!r}>"
        )
