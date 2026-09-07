"""
models/menu.py — Taomly Platform
Menu entities: Category, Product, ProductVariant, ModifierGroup, ModifierOption.

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
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, CheckConstraint, ForeignKey,
    Index, Integer, String, Text, Time, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


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

    restaurant   = relationship("Restaurant", back_populates="categories", lazy="select")
    products     = relationship("Product", back_populates="category", lazy="select")
    # Phase 4: локализованные названия категории.
    translations = relationship(
        "CategoryTranslation",
        back_populates="category",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

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
    # Phase 4: локализованные названия и описания продукта.
    translations    = relationship(
        "ProductTranslation",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
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

    product      = relationship("Product", back_populates="variants", lazy="select")
    # Phase 4: локализованные названия варианта.
    translations = relationship(
        "VariantTranslation",
        back_populates="variant",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

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
    # Phase 4: локализованные названия группы модификаторов.
    translations = relationship(
        "ModifierGroupTranslation",
        back_populates="modifier_group",
        cascade="all, delete-orphan",
        lazy="selectin",
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
    # Phase 4: локализованные названия опции модификатора.
    translations = relationship(
        "ModifierOptionTranslation",
        back_populates="modifier_option",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ModifierOption id={self.id} name={self.name!r} "
            f"price_adjustment={self.price_adjustment}>"
        )
