"""
models/orders.py — Taomly Platform
Orders: Order, OrderItem, OrderItemModifier.
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, CheckConstraint, Float,
    ForeignKey, Index, Integer, String, Text, TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


# ──────────────────────────────────────────
# ORDER
# ──────────────────────────────────────────
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
        Index("ix_orders_restaurant_status_created", "restaurant_id", "status", "created_at"),
        Index("ix_orders_client_telegram", "client_telegram_id"),
        # S1-3: index for location_id hot path.
        Index("ix_orders_location_id", "location_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    restaurant_id      = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # S1-3: location_id — canonical operational tenant scope for orders.
    # ON DELETE RESTRICT: physical deletion of a Location with historical
    # orders is forbidden. Soft delete (is_active=False) is the only
    # allowed deactivation path (ADR-005).
    # Migration 0015 will drop restaurant_id after full transition.
    location_id        = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_id          = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_telegram_id = Column(BigInteger, nullable=True)
    client_name        = Column(String(255))
    client_phone       = Column(String(50))
    order_type         = Column(String(20), nullable=False)
    address            = Column(Text)
    location_lat       = Column(Float)
    location_lng       = Column(Float)
    table_id           = Column(
        BigInteger,
        ForeignKey("restaurant_tables.id", ondelete="SET NULL"),
        nullable=True,
    )
    comment      = Column(Text)
    total_amount = Column(Integer, nullable=False)
    status       = Column(String(20), default="new", nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at   = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", back_populates="orders", lazy="select")
    # S1-3: location relationship — canonical operational scope.
    location   = relationship("Location", lazy="select")
    client     = relationship("User", lazy="select")
    items      = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status!r} total={self.total_amount}>"


# ──────────────────────────────────────────
# ORDER ITEM
# ──────────────────────────────────────────
class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_order_item_quantity"),
        CheckConstraint("price >= 0", name="ck_order_items_price_nonnegative"),
    )

    id         = Column(BigInteger, primary_key=True)
    order_id   = Column(
        BigInteger,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(
        BigInteger,
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    name     = Column(String(255), nullable=False)
    price    = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)

    # S2-2: Phase 2 — reserved for variant support (populated in S2-5).
    # variant_id:   FK → product_variants.id SET NULL. NULL for all pre-S2-5 orders.
    # variant_name: snapshot of variant name at order time. NULL for legacy orders.
    # These columns are intentionally inert until S2-5 activates variant order path.
    variant_id   = Column(
        BigInteger,
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    variant_name = Column(String(255), nullable=True)

    order   = relationship("Order", back_populates="items", lazy="select")
    product = relationship("Product", lazy="select")
    # S2-2: variant relationship — inert until S2-5.
    variant = relationship("ProductVariant", lazy="select")
    # S2-8: snapshot выбранных модификаторов на момент заказа.
    selected_modifiers = relationship(
        "OrderItemModifier",
        back_populates="order_item",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<OrderItem id={self.id} name={self.name!r} qty={self.quantity}>"


# ──────────────────────────────────────────
# ORDER ITEM MODIFIER  (S2-8)
# ──────────────────────────────────────────
class OrderItemModifier(Base):
    """
    Snapshot выбранной ModifierOption в позиции заказа.

    Один OrderItem → N записей (по одной на каждую выбранную опцию).

    Поля snapshot (берутся из БД на момент заказа, не от клиента):
      name:             имя опции (ModifierOption.name)
      price_adjustment: надбавка/скидка (ModifierOption.price_adjustment)

    ВАЖНО: OrderItem.price НЕ включает price_adjustment — Phase 7.

    Tenant-изоляция (через API, не через constraint):
      OrderItemModifier → order_item_id → OrderItem → order_id →
      Order → restaurant_id == JWT.restaurant_id

    Миграция: 0016_s2_8_order_item_modifiers.py

    FK поведение:
      order_item_id:      CASCADE DELETE (удаление OrderItem → удаление модификаторов)
      modifier_option_id: SET NULL (удаление ModifierOption сохраняет snapshot)
    """
    __tablename__ = "order_item_modifiers"
    __table_args__ = (
        Index("ix_order_item_modifiers_order_item_id", "order_item_id"),
        Index("ix_order_item_modifiers_option_id", "modifier_option_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    order_item_id      = Column(
        BigInteger,
        ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=False,
        index=False,  # covered by ix_order_item_modifiers_order_item_id
    )
    modifier_option_id = Column(
        BigInteger,
        ForeignKey("modifier_options.id", ondelete="SET NULL"),
        nullable=True,
        index=False,  # covered by ix_order_item_modifiers_option_id
    )
    # Snapshot полей ModifierOption на момент заказа.
    # Клиентские значения не принимаются — только из БД (ADR-S2-8-2).
    name             = Column(String(255), nullable=False)
    price_adjustment = Column(Integer, nullable=False)

    order_item = relationship("OrderItem", back_populates="selected_modifiers")

    def __repr__(self) -> str:
        return (
            f"<OrderItemModifier id={self.id} "
            f"order_item_id={self.order_item_id} "
            f"name={self.name!r} adj={self.price_adjustment}>"
        )
