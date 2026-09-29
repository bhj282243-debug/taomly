"""
models/delivery_zones.py — Taomly Platform
Phase 14: DeliveryZone — named delivery areas per Location.

Architecture (AQ-03 — Simple Named Zones):
  Location → DeliveryZone (1:many)
  DeliveryZone → Order.delivery_zone_id (nullable FK)

One DeliveryZone per named area (e.g. "Центр", "Юнусабад").
fee / min_order / eta_minutes are snapshotted into Order at checkout.
Changing zone config after Order creation does NOT affect historical Orders.

Tenant isolation: all zone operations validate zone.location_id → location.restaurant_id.
"""

# ruff: noqa: I001
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    __table_args__ = (
        CheckConstraint("fee >= 0",       name="ck_dz_fee_nonneg"),
        CheckConstraint("min_order >= 0", name="ck_dz_min_order_nonneg"),
        CheckConstraint(
            "eta_minutes IS NULL OR eta_minutes > 0",
            name="ck_dz_eta_positive",
        ),
        CheckConstraint("sort_order >= 0", name="ck_dz_sort_order_nonneg"),
        Index("ix_dz_location_active", "location_id", "is_active"),
    )

    id          = Column(BigInteger, primary_key=True)
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name        = Column(String(100), nullable=False)
    fee         = Column(Integer,  nullable=False, default=0, server_default="0")
    min_order   = Column(Integer,  nullable=False, default=0, server_default="0")
    eta_minutes = Column(Integer,  nullable=True)
    is_active   = Column(Boolean,  nullable=False, default=True, server_default="true")
    sort_order  = Column(Integer,  nullable=False, default=0,    server_default="0")
    created_at  = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at  = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    location = relationship("Location", back_populates="delivery_zones", lazy="select")
