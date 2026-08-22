"""
models.py — Taomly Platform
SQLAlchemy ORM-модели для Multi-Tenant White Label архитектуры.

Изменения v6 (Security — JWT Revocation):
  - RevokedToken: таблица отозванных JWT (jti revocation list).
    jti        — UUID4, уникальный идентификатор токена из JWT payload.
    token_type — "access" | "refresh". Зарезервировано для Refresh Token (пункт C-4).
                 Позволяет отзывать только refresh без инвалидации access, и наоборот.
    expires_at — копия exp из JWT. Записи старше expires_at — кандидаты на очистку.
    revoked_at — UTC-время отзыва (аудит, forensics).

    Индексы:
      uq_revoked_tokens_jti       — O(1) lookup при каждом запросе.
      ix_revoked_tokens_expires_at — ускоряет purge-запрос (DELETE WHERE expires_at < NOW()).

    Нет FK на Agency/Restaurant — намеренно. Удаление агентства не должно каскадно
    удалять revocation-записи: токены должны оставаться отозванными до истечения.

Изменения v3:
  - Product: добавлены badge-поля is_bestseller, is_new, is_spicy, is_chef_choice (M-2)
    Ранее бейджи кодировались в Product.description через #хэштеги — антипаттерн.
    Теперь отдельные булевые колонки:
      • SQL-индекс для AI-аналитики Этапа 2 (поиск хитов продаж)
      • Нет зависимости от текстового парсинга
      • Управляется через PATCH /api/menu/products/{id}
  - Product.price: задокументировано что цена хранится в сомах (целое число)
    L-2: без документации следующий разработчик не поймёт единицы измерения

  Миграция для существующей БД — MIGRATION_badges.sql

Изменения v4 (Security):
  - Product: добавлено is_popular (горизонтальный скролл "Популярное")

Изменения v5 (Delivery Settings):
  - Restaurant: добавлены working_hours, delivery_fee, min_order_amount
    Колонки добавлены миграцией 0004_add_delivery_fields.py.
    working_hours    — текстовое поле, например "10:00-22:00"
    delivery_fee     — стоимость доставки в сомах (0 = бесплатно)
    min_order_amount — минимальная сумма заказа в сомах (0 = без ограничений)
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, Float,
    ForeignKey, Index, Integer, String, Text,
    TIMESTAMP, UniqueConstraint, CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


# ──────────────────────────────────────────
# REVOKED TOKENS (JWT Revocation List)
# ──────────────────────────────────────────
class RevokedToken(Base):
    """
    JWT Revocation List — хранит отозванные токены до их естественного истечения.

    Архитектура:
      - При выдаче каждый JWT получает уникальный jti (UUID4).
      - При logout → INSERT jti сюда (атомарно через ON CONFLICT DO NOTHING).
      - При каждом запросе → SELECT по jti (индекс, O(1) ~1ms на Neon).
      - После exp токен устарел сам по себе → запись можно удалить.

    token_type зарезервирован для Refresh Token (пункт C-4):
      - "access"  — обычный access token (8 ч)
      - "refresh" — refresh token (7 дней, пункт C-4)
      Позволит отзывать refresh без инвалидации access, и наоборот.

    Очистка через purge_expired_revoked_tokens() в auth.py.
    Вызвать вручную или из maintenance-задачи:
        DELETE FROM revoked_tokens WHERE expires_at < NOW();
    """
    __tablename__ = "revoked_tokens"

    id         = Column(BigInteger, primary_key=True)
    jti        = Column(String(36), nullable=False)
    token_type = Column(String(16), nullable=False, server_default="access")  # access | refresh
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
        Index("ix_revoked_tokens_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<RevokedToken jti={self.jti!r} type={self.token_type!r} expires={self.expires_at}>"


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
# CATEGORY
# ──────────────────────────────────────────
class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "name", name="uq_category_restaurant_name"),
        Index("ix_categories_restaurant_sort", "restaurant_id", "sort_order"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name       = Column(String(255), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    restaurant = relationship("Restaurant", back_populates="categories", lazy="select")
    products   = relationship("Product", back_populates="category", lazy="select")

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r}>"


# ──────────────────────────────────────────
# PRODUCT
# ──────────────────────────────────────────
class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        Index("ix_products_restaurant_available_sort", "restaurant_id", "is_available", "sort_order"),
        Index("ix_products_category", "category_id"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id   = Column(
        BigInteger,
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    name         = Column(String(255), nullable=False)
    description  = Column(Text)

    # ЦЕНА: хранится в целых сомах (UZS).
    # Например: price=45000 → 45 000 сум.
    price        = Column(Integer, nullable=False)

    photo_url    = Column(Text)
    is_available = Column(Boolean, default=True, nullable=False)
    sort_order   = Column(Integer, default=0, nullable=False)

    # ── Badges ────────────────────────────────────────────────────────
    is_bestseller  = Column(Boolean, default=False, nullable=False, server_default="false")
    is_new         = Column(Boolean, default=False, nullable=False, server_default="false")
    is_spicy       = Column(Boolean, default=False, nullable=False, server_default="false")
    is_chef_choice = Column(Boolean, default=False, nullable=False, server_default="false")
    # is_popular: горизонтальный скролл "Популярное" на главном экране.
    # На этапе AI-2 заполняется автоматически из статистики заказов.
    is_popular     = Column(Boolean, default=False, nullable=False, server_default="false")

    updated_at   = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", back_populates="products", lazy="select")
    category   = relationship("Category", back_populates="products", lazy="select")

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.name!r} price={self.price}>"


# ──────────────────────────────────────────
# RESTAURANT TABLE
# ──────────────────────────────────────────
class RestaurantTable(Base):
    __tablename__ = "restaurant_tables"
    __table_args__ = (
        # S1-2: unique table_number within a Location.
        # Allows same table_number across different Locations of the same Brand.
        # uq_table_restaurant_number dropped in migration 0011.
        UniqueConstraint("location_id", "table_number", name="uq_table_location_number"),
    )

    id            = Column(BigInteger, primary_key=True)
    # Legacy: restaurant_id will be removed in migration 0015.
    # Kept for backward compat with all existing queries.
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S1-2: location_id — new tenant identity for tables.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_number = Column(String(50), nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    restaurant = relationship("Restaurant", back_populates="tables", lazy="select")
    # S1-2: location relationship — for future use; does not break existing code.
    location   = relationship("Location", lazy="select")

    def __repr__(self) -> str:
        return f"<RestaurantTable id={self.id} number={self.table_number!r}>"


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
    )

    id                 = Column(BigInteger, primary_key=True)
    restaurant_id      = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
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

    order   = relationship("Order", back_populates="items", lazy="select")
    product = relationship("Product", lazy="select")

    def __repr__(self) -> str:
        return f"<OrderItem id={self.id} name={self.name!r} qty={self.quantity}>"


# ──────────────────────────────────────────
# RESERVATION
# ──────────────────────────────────────────
class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','confirmed','completed','cancelled')",
            name="check_reservation_status",
        ),
        CheckConstraint("guests_count > 0", name="check_reservation_guests"),
        Index("ix_reservations_restaurant_time", "restaurant_id", "reservation_time"),
    )

    id               = Column(BigInteger, primary_key=True)
    restaurant_id    = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_name      = Column(String(255), nullable=False)
    client_phone     = Column(String(50), nullable=False)
    guests_count     = Column(Integer, nullable=False)
    reservation_time = Column(TIMESTAMP(timezone=True), nullable=False)
    comment          = Column(Text)
    status           = Column(String(20), default="new", nullable=False)
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", back_populates="reservations", lazy="select")

    def __repr__(self) -> str:
        return f"<Reservation id={self.id} client={self.client_name!r} status={self.status!r}>"


# ──────────────────────────────────────────
# WAITER CALL
# ──────────────────────────────────────────
class WaiterCall(Base):
    __tablename__ = "waiter_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','accepted','completed','cancelled')",
            name="check_waiter_call_status",
        ),
        Index("ix_waiter_calls_restaurant_status", "restaurant_id", "status"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_id   = Column(
        BigInteger,
        ForeignKey("restaurant_tables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status     = Column(String(20), default="active", nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", lazy="select")
    table      = relationship("RestaurantTable", lazy="select")

    def __repr__(self) -> str:
        return f"<WaiterCall id={self.id} table_id={self.table_id} status={self.status!r}>"


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
    event_type = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<UsageEvent id={self.id} restaurant_id={self.restaurant_id} type={self.event_type!r}>"


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
