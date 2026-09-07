"""
models/tenant.py — Taomly Platform
Tenant hierarchy: Agency → Restaurant → Location, User.

Изменения v5 (Delivery Settings):
  - Restaurant: добавлены working_hours, delivery_fee, min_order_amount
    Колонки добавлены миграцией 0004_add_delivery_fields.py.
    working_hours    — текстовое поле, например "10:00-22:00"
    delivery_fee     — стоимость доставки в сомах (0 = бесплатно)
    min_order_amount — минимальная сумма заказа в сомах (0 = без ограничений)

Location (Stage 1):
  Архитектура:
    Agency → Restaurant (Brand) → Location → Tables / Orders / Reservations

  Каждый существующий Restaurant получает ровно одну Location при backfill
  (migration 0010). Дальнейшие изменения tenant identity (orders, tables, etc.)
  — в последующих миграциях (S1-2 ... S1-4).

  ADR-001: 1 Location = 1 Telegram Bot (Stage 1 locked decision).
  ADR-002: location.id = immutable DB identity, location.slug = mutable public identifier.
  ADR-005: soft delete (is_active=False) — физическое удаление Location с Orders
           запрещено через ON DELETE RESTRICT на orders.location_id (S1-3).
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, CheckConstraint, Index, Integer,
    String, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import ForeignKey

from database import Base


# ──────────────────────────────────────────
# AGENCY
# ──────────────────────────────────────────
class Agency(Base):
    __tablename__ = "agencies"

    id                  = Column(BigInteger, primary_key=True)
    name                = Column(String(255), nullable=False)
    owner_email         = Column(String(255), unique=True, nullable=False, index=True)
    owner_password_hash = Column(String(255), nullable=False)
    is_active           = Column(Boolean, default=True, nullable=False)
    created_at          = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at          = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurants = relationship("Restaurant", back_populates="agency", lazy="select")

    def __repr__(self) -> str:
        return f"<Agency id={self.id} name={self.name!r}>"


# ──────────────────────────────────────────
# RESTAURANT
# ──────────────────────────────────────────
class Restaurant(Base):
    __tablename__ = "restaurants"
    __table_args__ = (
        Index("ix_restaurants_agency_active", "agency_id", "is_active"),
        CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_restaurants_currency",
        ),
        CheckConstraint(
            "language IN ('uz', 'ru', 'en')",
            name="ck_restaurants_language",
        ),
    )

    id          = Column(BigInteger, primary_key=True)
    agency_id   = Column(
        BigInteger,
        ForeignKey("agencies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name        = Column(String(255), nullable=False)
    slug        = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text)
    phone       = Column(String(50))
    address     = Column(Text)
    is_active   = Column(Boolean, default=True, nullable=False)
    is_waiter_call_enabled = Column(Boolean, default=False, nullable=False)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    admin_password_hash = Column(String(255), nullable=True)

    # White Label Branding
    logo_url        = Column(Text, nullable=True)
    primary_color   = Column(String(20), default="#8B1A2E", nullable=False)
    secondary_color = Column(String(20), default="#FAF6EE", nullable=False)
    accent_color    = Column(String(20), default="#D4A853", nullable=False)
    welcome_text    = Column(Text, nullable=True)
    custom_domain   = Column(String(255), nullable=True, unique=True, index=True)

    # Telegram White Label
    telegram_bot_token_encrypted = Column(Text, nullable=True)
    telegram_dispatcher_id       = Column(BigInteger, nullable=True)

    # Delivery Settings (миграция 0004_add_delivery_fields)
    # working_hours    — часы работы, отображаются клиенту на Hero-экране
    # delivery_fee     — стоимость доставки в сомах; 0 = бесплатно
    # min_order_amount — минимальная сумма заказа в сомах; 0 = без ограничений
    # timezone         — IANA timezone для пиковых часов в аналитике,
    #                    например "Asia/Tashkent" (UTC+5), "Asia/Almaty" (UTC+5/+6)
    #                    Дефолт "Asia/Tashkent" — основной рынок (Узбекистан).
    working_hours    = Column(String(50), nullable=True)
    delivery_fee     = Column(Integer, default=0, nullable=False, server_default="0")
    min_order_amount = Column(Integer, default=0, nullable=False, server_default="0")
    timezone         = Column(String(64), nullable=True, server_default="Asia/Tashkent")
    # currency — валюта ресторана для отображения цен клиентам и в уведомлениях.
    # НЕ путать с SubscriptionPlan.currency (биллинговая валюта тарифа).
    # Допустимые значения: UZS, KZT, RUB, USD, TRY, AED.
    # Добавлено миграцией 0007_add_restaurant_currency.
    currency         = Column(String(10), nullable=False, server_default="UZS", default="UZS")
    # language — язык клиентского UI и Telegram-уведомлений для клиентов ресторана.
    # Допустимые значения: uz, ru, en.
    # Дефолт 'uz' — основной рынок (Узбекистан).
    # Добавлено миграцией 0008_add_restaurant_language.
    language         = Column(String(5), nullable=False, server_default="uz", default="uz")

    agency       = relationship("Agency", back_populates="restaurants", lazy="select")
    categories   = relationship("Category", back_populates="restaurant", lazy="select")
    products     = relationship("Product", back_populates="restaurant", lazy="select")
    orders       = relationship("Order", back_populates="restaurant", lazy="select")
    reservations = relationship("Reservation", back_populates="restaurant", lazy="select")
    tables       = relationship("RestaurantTable", back_populates="restaurant", lazy="select")

    def __repr__(self) -> str:
        return f"<Restaurant id={self.id} slug={self.slug!r}>"


# ──────────────────────────────────────────
# USER (Telegram-клиент)
# ──────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','owner','dispatcher','client')",
            name="check_user_role",
        ),
        UniqueConstraint("restaurant_id", "telegram_id", name="uq_user_restaurant_telegram"),
        Index("ix_users_restaurant_role", "restaurant_id", "role"),
    )

    id            = Column(BigInteger, primary_key=True)
    telegram_id   = Column(BigInteger, nullable=False, index=True)
    name          = Column(String(255))
    phone         = Column(String(50))
    role          = Column(String(20), nullable=False, default="client")
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id} role={self.role!r}>"


# ──────────────────────────────────────────
# LOCATION
# Stage 1: физическая точка присутствия бренда.
#
# Архитектура:
#   Agency → Restaurant (Brand) → Location → Tables / Orders / Reservations
#
# Каждый существующий Restaurant получает ровно одну Location при backfill
# (migration 0010). Дальнейшие изменения tenant identity (orders, tables, etc.)
# — в последующих миграциях (S1-2 ... S1-4).
#
# ADR-001: 1 Location = 1 Telegram Bot (Stage 1 locked decision).
# ADR-002: location.id = immutable DB identity, location.slug = mutable public identifier.
# ADR-005: soft delete (is_active=False) — физическое удаление Location с Orders
#          запрещено через ON DELETE RESTRICT на orders.location_id (S1-3).
# ──────────────────────────────────────────
class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_locations_slug"),
        Index("ix_locations_restaurant_active", "restaurant_id", "is_active"),
        CheckConstraint(
            "delivery_fee >= 0",
            name="ck_locations_delivery_fee_nonnegative",
        ),
        CheckConstraint(
            "min_order_amount >= 0",
            name="ck_locations_min_order_amount_nonnegative",
        ),
        CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_locations_currency",
        ),
        CheckConstraint(
            "language IN ('uz', 'ru', 'en')",
            name="ck_locations_language",
        ),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name     = Column(String(255), nullable=False)
    slug     = Column(String(100), nullable=False)
    # slug: globally UNIQUE. Public routing identifier (webhook, URL, QR).
    # Mutable — slug may be renamed; id is the immutable DB identity.
    # Backfill: location.slug = restaurant.slug for the initial Location.

    is_active = Column(Boolean, default=True, nullable=False)

    address = Column(Text, nullable=True)
    phone   = Column(String(50), nullable=True)

    # Operational settings (moved from Restaurant in Stage 1)
    timezone         = Column(String(64), nullable=False, server_default="Asia/Tashkent")
    working_hours    = Column(String(100), nullable=True)
    # 100 chars: "Пн-Пт 10:00-22:00, Сб-Вс 11:00-23:00" fits comfortably
    delivery_fee     = Column(Integer, default=0, nullable=False, server_default="0")
    min_order_amount = Column(Integer, default=0, nullable=False, server_default="0")
    currency         = Column(String(10), nullable=False, server_default="UZS", default="UZS")
    language         = Column(String(5), nullable=False, server_default="uz", default="uz")

    is_waiter_call_enabled = Column(Boolean, default=False, nullable=False)

    # Telegram config (per-location, ADR-001: 1 Location = 1 Bot)
    # Moved here from Restaurant in Stage 1. Restaurant fields kept for
    # backward compat until Migration 0015 (DROP legacy columns).
    telegram_bot_token_encrypted = Column(Text, nullable=True)
    telegram_dispatcher_id       = Column(BigInteger, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", lazy="select")

    def __repr__(self) -> str:
        return f"<Location id={self.id} slug={self.slug!r} restaurant_id={self.restaurant_id}>"
