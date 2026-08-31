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

Изменения S2-2 (Phase 2 — Menu Engine Foundation):
  Migration 0014. Только новые таблицы и два nullable-поля в order_items.
  Product.price НЕ изменяется (остаётся NOT NULL).

  ProductVariant — варианты товара (Плов: Полная порция / Половина).
    product_id  → products.id CASCADE
    price       — цена варианта в целых сомах, CHECK >= 0
    is_active   — управляется админом вручную

  ModifierGroup — группа модификаторов (Дополнительно).
    product_id     → products.id CASCADE
    min_selections — 0 = необязательная, >= 1 = обязательная (поле required отсутствует)
    max_selections — 1 = radio, > 1 = checkbox

  ModifierOption — опция модификатора (Extra meat +10 000).
    modifier_group_id → modifier_groups.id CASCADE
    price_adjustment  — знаковое целое, CHECK >= -1 000 000

  OrderItem — добавлены два nullable-поля для будущей S2-5:
    variant_id   → product_variants.id SET NULL, nullable
    variant_name — snapshot имени варианта на момент заказа, nullable
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, Float,
    ForeignKey, Index, Integer, String, Text,
    TIMESTAMP, Time, UniqueConstraint, CheckConstraint,
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
        # S2-5: NULL разрешён для variant-продуктов; price >= 0 для legacy-продуктов.
        CheckConstraint("price IS NULL OR price >= 0", name="ck_products_price_nonneg_or_null"),
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
    # S2-5: nullable — NULL для продуктов с вариантами (цена берётся из ProductVariant).
    #        NOT NULL для legacy-продуктов без вариантов.
    price        = Column(Integer, nullable=True)

    photo_url    = Column(Text)
    is_available = Column(Boolean, default=True, nullable=False)
    sort_order   = Column(Integer, default=0, nullable=False)

    # Phase 3: расписание доступности (TIME без timezone, локальное время ресторана).
    # NULL/NULL = нет расписания (доступность определяется только is_available).
    # from < until  → нормальное окно: 11:00–22:00
    # from > until  → overnight:       22:00–02:00
    # from == until → 24 часа (всегда доступно)
    # Timezone для вычисления берётся из Location.timezone (runtime source of truth).
    available_from  = Column(Time(), nullable=True)
    available_until = Column(Time(), nullable=True)

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

    restaurant      = relationship("Restaurant", back_populates="products", lazy="select")
    category        = relationship("Category", back_populates="products", lazy="select")
    # S2-2: Phase 2 Menu Engine relationships
    variants        = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProductVariant.sort_order",
    )
    modifier_groups = relationship(
        "ModifierGroup",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ModifierGroup.sort_order",
    )

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


# ──────────────────────────────────────────
# PRODUCT VARIANT  (S2-2 / Phase 2)
# ──────────────────────────────────────────
class ProductVariant(Base):
    """
    Вариант товара — одна покупаемая версия продукта.

    Архитектура:
      Product (0 вариантов) → используется Product.price (legacy/simple режим).
      Product (1+ вариантов) → каждый вариант имеет собственную цену.
        Product.price в этом случае игнорируется (станет nullable в S2-5).

    Примеры:
      Плов → [Полная порция 35 000, Половина 20 000]
      Компот → [1 L 20 000, 0.7 L 15 000]

    Tenant-изоляция:
      ProductVariant → product_id → Product.restaurant_id
      API никогда не принимает variant_id без проверки через Product.

    is_active: управляется администратором вручную.
      Phase 3 добавит расписание для автоматической деактивации.

    sort_order: порядок отображения вариантов в UI.
      Первый активный вариант — дефолтный выбор.

    Миграция: 0014_phase2_menu_engine.py
    """
    __tablename__ = "product_variants"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_product_variants_price_nonnegative"),
        Index("ix_variants_product_id", "product_id"),
        Index("ix_variants_product_active_sort", "product_id", "is_active", "sort_order"),
    )

    id         = Column(BigInteger, primary_key=True)
    product_id = Column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=False,  # covered by ix_variants_product_id above
    )
    name       = Column(String(255), nullable=False)
    # price: цена варианта в целых сомах. CHECK >= 0 (бесплатные варианты допустимы).
    price      = Column(Integer, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False, server_default="0")
    is_active     = Column(Boolean, default=True, nullable=False, server_default="true")
    # Phase 3: временная недоступность (sold-out).
    # is_active=false  → скрыт полностью (admin отключил)
    # is_available=false → виден, но disabled ("Sold out", временно)
    # is_available=true  → доступен для выбора
    is_available  = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product = relationship("Product", back_populates="variants", lazy="select")

    def __repr__(self) -> str:
        return f"<ProductVariant id={self.id} name={self.name!r} price={self.price}>"


# ──────────────────────────────────────────
# MODIFIER GROUP  (S2-2 / Phase 2)
# ──────────────────────────────────────────
class ModifierGroup(Base):
    """
    Группа модификаторов — набор опций для кастомизации блюда.

    Примеры:
      "Дополнительно" → [Extra meat +10 000, Яйцо +5 000, Острое +2 000]
      "Соус" → [Кетчуп 0, Майонез 0, Сырный +3 000]

    Семантика обязательности (поле required ОТСУТСТВУЕТ — намеренно):
      min_selections = 0 → группа необязательная
      min_selections >= 1 → группа обязательная (клиент обязан выбрать)

    Семантика выбора:
      max_selections = 1 → radio (только одна опция)
      max_selections > 1 → checkbox (несколько опций)

    Constraints:
      min_selections >= 0
      max_selections >= 1
      max_selections >= min_selections

    Tenant-изоляция:
      ModifierGroup → product_id → Product.restaurant_id

    Миграция: 0014_phase2_menu_engine.py
    """
    __tablename__ = "modifier_groups"
    __table_args__ = (
        CheckConstraint("min_selections >= 0", name="ck_modifier_groups_min_selections_nonneg"),
        CheckConstraint("max_selections >= 1", name="ck_modifier_groups_max_selections_positive"),
        CheckConstraint("max_selections >= min_selections", name="ck_modifier_groups_max_gte_min"),
        Index("ix_modifier_groups_product_id", "product_id"),
        Index("ix_modifier_groups_product_active", "product_id", "is_active"),
    )

    id         = Column(BigInteger, primary_key=True)
    product_id = Column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=False,  # covered by ix_modifier_groups_product_id above
    )
    name           = Column(String(255), nullable=False)
    # min_selections: 0 = необязательная группа, >= 1 = обязательная.
    min_selections = Column(Integer, default=0, nullable=False, server_default="0")
    # max_selections: 1 = radio, > 1 = checkbox.
    max_selections = Column(Integer, default=1, nullable=False, server_default="1")
    sort_order     = Column(Integer, default=0, nullable=False, server_default="0")
    is_active      = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at     = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at     = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product = relationship("Product", back_populates="modifier_groups", lazy="select")
    options = relationship(
        "ModifierOption",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ModifierOption.sort_order",
    )

    def __repr__(self) -> str:
        return (
            f"<ModifierGroup id={self.id} name={self.name!r} "
            f"min={self.min_selections} max={self.max_selections}>"
        )


# ──────────────────────────────────────────
# MODIFIER OPTION  (S2-2 / Phase 2)
# ──────────────────────────────────────────
class ModifierOption(Base):
    """
    Опция модификатора — одна позиция внутри группы.

    Примеры:
      Extra meat    price_adjustment=+10000
      Яйцо          price_adjustment=+5000
      Острое        price_adjustment=+2000
      Стандартный   price_adjustment=0
      Скидка        price_adjustment=-5000  (отрицательные разрешены)

    price_adjustment: знаковое целое в сомах.
      Итоговая цена: variant.price + sum(selected options.price_adjustment)
      Реализуется в Phase 7 (Order Engine полный рефакторинг).
      CHECK: price_adjustment >= -1 000 000 (защита от абсурдных скидок).

    Tenant-изоляция:
      ModifierOption → modifier_group_id → ModifierGroup → product_id → Product.restaurant_id

    Миграция: 0014_phase2_menu_engine.py
    """
    __tablename__ = "modifier_options"
    __table_args__ = (
        CheckConstraint(
            "price_adjustment >= -1000000",
            name="ck_modifier_options_price_adjustment_range",
        ),
        Index("ix_modifier_options_group_id", "modifier_group_id"),
        Index(
            "ix_modifier_options_group_active_sort",
            "modifier_group_id", "is_active", "sort_order",
        ),
    )

    id                = Column(BigInteger, primary_key=True)
    modifier_group_id = Column(
        BigInteger,
        ForeignKey("modifier_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=False,  # covered by ix_modifier_options_group_id above
    )
    name             = Column(String(255), nullable=False)
    # price_adjustment: надбавка (> 0) или скидка (< 0) в сомах. 0 = бесплатно.
    price_adjustment = Column(Integer, default=0, nullable=False, server_default="0")
    sort_order       = Column(Integer, default=0, nullable=False, server_default="0")
    is_active        = Column(Boolean, default=True, nullable=False, server_default="true")
    # Phase 3: временная недоступность опции (sold-out).
    # is_active=false    → скрыта полностью
    # is_available=false → видна, но disabled ("Нет в наличии")
    # ModifierGroup.is_available НЕ добавляется — группы управляются только через is_active.
    is_available     = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    group = relationship("ModifierGroup", back_populates="options", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<ModifierOption id={self.id} name={self.name!r} "
            f"price_adjustment={self.price_adjustment}>"
        )


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
    # S1-4: Location-level tenant scope.
    # ON DELETE RESTRICT: бронь — исторический документ; Location с бронями
    # физически удалить нельзя. Soft delete (is_active=False) — единственный
    # допустимый способ деактивации.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="RESTRICT"),
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
    location   = relationship("Location", lazy="select")

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
    # S1-4: Location-level tenant scope.
    # ON DELETE CASCADE: вызов официанта — оперативная запись.
    # При удалении Location вызовы удаляются вместе с ней.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="CASCADE"),
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
    location   = relationship("Location", lazy="select")
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
