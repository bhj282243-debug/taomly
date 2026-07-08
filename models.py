"""
models.py — Taomly Platform
SQLAlchemy ORM-модели для Multi-Tenant White Label архитектуры.

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
    )

    id          = Column(BigInteger, primary_key=True)

    # NOT NULL: ресторан обязан принадлежать агентству.
    # RESTRICT: нельзя удалить агентство, пока у него есть рестораны.
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

    # Аутентификация ресторанного администратора
    admin_password_hash = Column(String(255), nullable=True)

    # White Label Branding
    logo_url        = Column(Text, nullable=True)
    primary_color   = Column(String(20), default="#8B1A2E", nullable=False)
    secondary_color = Column(String(20), default="#FAF6EE", nullable=False)
    accent_color    = Column(String(20), default="#D4A853", nullable=False)
    welcome_text    = Column(Text, nullable=True)
    custom_domain   = Column(String(255), nullable=True, unique=True, index=True)

    # Telegram White Label: у каждого ресторана свой бот.
    # Токен хранится зашифрованным через Fernet.
    telegram_bot_token_encrypted = Column(Text, nullable=True)
    telegram_dispatcher_id       = Column(BigInteger, nullable=True)

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
        # Multi-Tenant: один Telegram-пользователь может быть клиентом
        # нескольких ресторанов — уникальность только внутри ресторана.
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
    # Отображать: f"{price:,} so'm"
    # При выходе на международный рынок (Этап 3+) заменить на Numeric(12, 2)
    # и добавить колонку currency_code.
    price        = Column(Integer, nullable=False)

    photo_url    = Column(Text)
    is_available = Column(Boolean, default=True, nullable=False)
    sort_order   = Column(Integer, default=0, nullable=False)

    # ── Badges (M-2) ──────────────────────────────────────────────────
    # Заменяют #хэштеги в description. Отдельные булевые колонки:
    #   - быстрый SQL-запрос без LIKE '%#bestseller%'
    #   - готовы к AI-аналитике Этапа 2 (топ блюд, рекомендации)
    #   - управляются через PATCH /api/menu/products/{id}
    # Миграция существующей БД: MIGRATION_badges.sql
    is_bestseller = Column(Boolean, default=False, nullable=False, server_default="false")
    is_new        = Column(Boolean, default=False, nullable=False, server_default="false")
    is_spicy      = Column(Boolean, default=False, nullable=False, server_default="false")
    is_chef_choice = Column(Boolean, default=False, nullable=False, server_default="false")

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
        UniqueConstraint("restaurant_id", "table_number", name="uq_table_restaurant_number"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_number = Column(String(50), nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    restaurant = relationship("Restaurant", back_populates="tables", lazy="select")

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
    """
    Тарифные планы платформы.
    Данные вставляются через MIGRATION_billing.sql (Free, Basic, Pro).
    Цена хранится в целых единицах выбранной валюты.
    """
    __tablename__ = "subscription_plans"

    id                = Column(Integer, primary_key=True)
    name              = Column(String(50), unique=True, nullable=False)   # Free / Basic / Pro
    price             = Column(Integer, nullable=False, default=0)        # 0 / 29 / 79
    currency          = Column(String(10), nullable=False, default="USD")
    orders_per_month  = Column(Integer, nullable=False, default=100)      # -1 = безлимит
    products_limit    = Column(Integer, nullable=False, default=20)       # -1 = безлимит
    users_limit       = Column(Integer, nullable=False, default=-1)       # -1 = безлимит (пока)
    description       = Column(Text, nullable=True)
    is_active         = Column(Boolean, default=True, nullable=False)

    subscriptions = relationship("Subscription", back_populates="plan", lazy="select")

    def __repr__(self) -> str:
        return f"<SubscriptionPlan id={self.id} name={self.name!r} price={self.price}>"


# ──────────────────────────────────────────
# BILLING — Subscriptions
# ──────────────────────────────────────────
class Subscription(Base):
    """
    Текущая и история подписок ресторана.
    is_active=True — активная подписка (не более одной на ресторан).
    expires_at=NULL — подписка бессрочная (Free / тестовый период).
    """
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
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)   # NULL = бессрочно
    is_active  = Column(Boolean, default=True, nullable=False)

    restaurant = relationship("Restaurant", lazy="select")
    plan       = relationship("SubscriptionPlan", back_populates="subscriptions", lazy="select")

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} restaurant_id={self.restaurant_id} plan_id={self.plan_id}>"


# ──────────────────────────────────────────
# BILLING — Usage Events
# ──────────────────────────────────────────
class UsageEvent(Base):
    """
    Лог использования ресурсов платформы.
    event_type: 'order_created' | 'product_created' | 'product_deleted'
    Используется для аудита и будущего billing-by-usage.
    Текущий подсчёт (usage endpoint) идёт напрямую через SQL COUNT — быстро и точно.
    UsageEvent — для истории и аудита.
    """
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
