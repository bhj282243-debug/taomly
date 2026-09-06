"""
schemas/menu_public.py — Taomly Platform

Public menu schemas with translation support (Phase 4).
Depends on schemas.localization (must be imported after localization.py is ready).

Dependency chain:
    schemas.localization
          ↓
    schemas.menu_public
"""

from datetime import datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.localization import (
    CategoryTranslationResponse,
    NameTranslationResponse,
    ProductTranslationResponse,
)


# ──────────────────────────────────────────
# Расширенные admin-response schemas (для GET /api/menu/{id}/all)
# Добавляем поле translations: [] без изменения существующих полей.
# ──────────────────────────────────────────

class VariantResponseWithTranslations(BaseModel):
    id:           int
    product_id:   int
    name:         str
    price:        int
    sort_order:   int
    is_active:    bool
    is_available: bool
    created_at:   datetime
    updated_at:   datetime
    translations: List[NameTranslationResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ModifierOptionResponseWithTranslations(BaseModel):
    id:               int
    modifier_group_id: int
    name:             str
    price_adjustment: int
    sort_order:       int
    is_active:        bool
    is_available:     bool
    created_at:       datetime
    updated_at:       datetime
    translations: List[NameTranslationResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ModifierGroupResponseWithTranslations(BaseModel):
    id:             int
    product_id:     int
    name:           str
    min_selections: int
    max_selections: int
    sort_order:     int
    is_active:      bool
    created_at:     datetime
    updated_at:     datetime
    translations: List[NameTranslationResponse] = Field(default_factory=list)
    options: List[ModifierOptionResponseWithTranslations] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ProductResponseWithTranslations(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price: Optional[int] = None
    photo_url: Optional[str] = None
    is_available: bool
    sort_order: int
    is_bestseller: bool = False
    is_new: bool = False
    is_spicy: bool = False
    is_chef_choice: bool = False
    is_popular: bool = False
    available_from:  Optional[time] = None
    available_until: Optional[time] = None
    translations: List[ProductTranslationResponse] = Field(default_factory=list)
    variants: List[VariantResponseWithTranslations] = Field(default_factory=list)
    modifier_groups: List[ModifierGroupResponseWithTranslations] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class CategoryResponseWithTranslations(BaseModel):
    id: int
    name: str
    sort_order: int
    translations: List[CategoryTranslationResponse] = Field(default_factory=list)
    products: List[ProductResponseWithTranslations] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
