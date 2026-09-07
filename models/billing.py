"""
models/billing.py — Taomly Platform
Billing: Subscription Plans, Subscriptions, Usage Events.
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, Index, Integer, String, Text,
    TIMESTAMP, CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import ForeignKey

from database import Base


# ──────────────────────────────────────────
# BILLING — Subscription Plans
# ──────────────────────────────────────────
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id                = Column(Integer, primary_key=True)
    name              = Column(String(50), unique=True, nullable=False)
    price             = Column(Integer, nullable=False, default=0)
    currency          = Column(String(10), nullable=False, default="USD")
    orders_per_month  = Column(Integer, nullable=False, default=100)
    products_limit    = Column(Integer, nullable=False, default=20)
    users_limit       = Column(Integer, nullable=False, default=-1)
    description       = Column(Text, nullable=True)
    is_active         = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_subscription_plans_price_nonnegative"),
    )

    subscriptions = relationship("Subscription", back_populates="plan", lazy="select")

    def __repr__(self) -> str:
        return f"<SubscriptionPlan id={self.id} name={self.name!r} price={self.price}>"


# ──────────────────────────────────────────
# BILLING — Subscriptions
# ──────────────────────────────────────────
class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_restaurant_active", "restaurant_id", "is_active"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id    = Column(Integer, ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    started_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)
    is_active  = Column(Boolean, default=True, nullable=False)

    restaurant = relationship("Restaurant", lazy="select")
    plan       = relationship("SubscriptionPlan", back_populates="subscriptions", lazy="select")

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} restaurant_id={self.restaurant_id} plan_id={self.plan_id}>"


# ──────────────────────────────────────────
# BILLING — Usage Events
# ──────────────────────────────────────────
class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('order_created','product_created','product_deleted')",
            name="check_usage_event_type",
        ),
        Index("ix_usage_events_restaurant_month", "restaurant_id", "created_at"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S1-4: Location-level scope (nullable).
    # ON DELETE SET NULL: исторические billing events не удаляются при закрытии Location.
    # location_id становится NULL — event сохраняется для аудита.
    # Billing quota считается по Brand (restaurant_id) — location_id аналитический.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<UsageEvent id={self.id} restaurant_id={self.restaurant_id} type={self.event_type!r}>"
