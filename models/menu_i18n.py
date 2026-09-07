"""
models/menu_i18n.py — Taomly Platform
Phase 4: Menu Localization translation tables.

Принцип:
  - entity.name / entity.description = legacy/base fallback (НЕ удаляются)
  - *Translation = source of truth для конкретного language
  - ON DELETE CASCADE: удаление сущности → переводы удаляются автоматически
  - UNIQUE(entity_id, language): дубликаты запрещены на уровне БД
  - language CHECK: только MENU_LANGUAGES = {"uz", "ru", "en"}

Миграция: 0018_phase4_menu_localization.py
"""

from sqlalchemy import (
    BigInteger, Column, CheckConstraint, ForeignKey,
    Index, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


# ══════════════════════════════════════════
# PHASE 4 — MENU LOCALIZATION MODELS
# ══════════════════════════════════════════


class CategoryTranslation(Base):
    """
    Локализованное название категории меню.

    Tenant-изоляция: category_id → categories.restaurant_id
    Миграция: 0018_phase4_menu_localization.py
    """
    __tablename__ = "category_translations"
    __table_args__ = (
        UniqueConstraint("category_id", "language", name="uq_cat_trans_category_lang"),
        CheckConstraint("language IN ('uz','ru','en')", name="ck_cat_trans_language"),
        CheckConstraint("length(trim(name)) > 0", name="ck_cat_trans_name_nonempty"),
        Index("ix_cat_trans_category_id", "category_id"),
    )

    id          = Column(BigInteger, primary_key=True)
    category_id = Column(
        BigInteger,
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
        index=False,  # covered by ix_cat_trans_category_id
    )
    language    = Column(String(5), nullable=False)
    name        = Column(String(255), nullable=False)

    category = relationship("Category", back_populates="translations")

    def __repr__(self) -> str:
        return f"<CategoryTranslation category_id={self.category_id} lang={self.language!r}>"


class ProductTranslation(Base):
    """
    Локализованное название и описание продукта.

    description — опционально (nullable), как и в исходном Product.description.
    Tenant-изоляция: product_id → products.restaurant_id
    Миграция: 0018_phase4_menu_localization.py
    """
    __tablename__ = "product_translations"
    __table_args__ = (
        UniqueConstraint("product_id", "language", name="uq_prod_trans_product_lang"),
        CheckConstraint("language IN ('uz','ru','en')", name="ck_prod_trans_language"),
        CheckConstraint("length(trim(name)) > 0", name="ck_prod_trans_name_nonempty"),
        Index("ix_prod_trans_product_id", "product_id"),
    )

    id          = Column(BigInteger, primary_key=True)
    product_id  = Column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=False,
    )
    language    = Column(String(5), nullable=False)
    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    product = relationship("Product", back_populates="translations")

    def __repr__(self) -> str:
        return f"<ProductTranslation product_id={self.product_id} lang={self.language!r}>"


class VariantTranslation(Base):
    """
    Локализованное название варианта продукта.

    Tenant-изоляция: variant_id → product_variants.product_id → products.restaurant_id
    Миграция: 0018_phase4_menu_localization.py
    """
    __tablename__ = "variant_translations"
    __table_args__ = (
        UniqueConstraint("variant_id", "language", name="uq_var_trans_variant_lang"),
        CheckConstraint("language IN ('uz','ru','en')", name="ck_var_trans_language"),
        CheckConstraint("length(trim(name)) > 0", name="ck_var_trans_name_nonempty"),
        Index("ix_var_trans_variant_id", "variant_id"),
    )

    id         = Column(BigInteger, primary_key=True)
    variant_id = Column(
        BigInteger,
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=False,
    )
    language   = Column(String(5), nullable=False)
    name       = Column(String(255), nullable=False)

    variant = relationship("ProductVariant", back_populates="translations")

    def __repr__(self) -> str:
        return f"<VariantTranslation variant_id={self.variant_id} lang={self.language!r}>"


class ModifierGroupTranslation(Base):
    """
    Локализованное название группы модификаторов.

    Tenant-изоляция: modifier_group_id → modifier_groups.product_id → products.restaurant_id
    Миграция: 0018_phase4_menu_localization.py
    """
    __tablename__ = "modifier_group_translations"
    __table_args__ = (
        UniqueConstraint("modifier_group_id", "language", name="uq_modgrp_trans_group_lang"),
        CheckConstraint("language IN ('uz','ru','en')", name="ck_modgrp_trans_language"),
        CheckConstraint("length(trim(name)) > 0", name="ck_modgrp_trans_name_nonempty"),
        Index("ix_modgrp_trans_group_id", "modifier_group_id"),
    )

    id                = Column(BigInteger, primary_key=True)
    modifier_group_id = Column(
        BigInteger,
        ForeignKey("modifier_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=False,
    )
    language          = Column(String(5), nullable=False)
    name              = Column(String(255), nullable=False)

    modifier_group = relationship("ModifierGroup", back_populates="translations")

    def __repr__(self) -> str:
        return (
            f"<ModifierGroupTranslation modifier_group_id={self.modifier_group_id} "
            f"lang={self.language!r}>"
        )


class ModifierOptionTranslation(Base):
    """
    Локализованное название опции модификатора.

    Tenant-изоляция: modifier_option_id → modifier_options.modifier_group_id
                     → modifier_groups.product_id → products.restaurant_id
    Миграция: 0018_phase4_menu_localization.py
    """
    __tablename__ = "modifier_option_translations"
    __table_args__ = (
        UniqueConstraint("modifier_option_id", "language", name="uq_modopt_trans_option_lang"),
        CheckConstraint("language IN ('uz','ru','en')", name="ck_modopt_trans_language"),
        CheckConstraint("length(trim(name)) > 0", name="ck_modopt_trans_name_nonempty"),
        Index("ix_modopt_trans_option_id", "modifier_option_id"),
    )

    id                 = Column(BigInteger, primary_key=True)
    modifier_option_id = Column(
        BigInteger,
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=False,
        index=False,
    )
    language           = Column(String(5), nullable=False)
    name               = Column(String(255), nullable=False)

    modifier_option = relationship("ModifierOption", back_populates="translations")

    def __repr__(self) -> str:
        return (
            f"<ModifierOptionTranslation modifier_option_id={self.modifier_option_id} "
            f"lang={self.language!r}>"
        )
