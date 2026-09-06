"""
schemas/menu.py — Taomly Platform

Admin CRUD schemas: Product, Category, Variant, ModifierGroup, ModifierOption.
"""

from datetime import datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.common import _validate_url


# ──────────────────────────────────────────
# ПРОДУКТЫ (admin response)
# ──────────────────────────────────────────

class ProductResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    # S2-5: nullable — None для variant-продуктов (цена на уровне variants).
    price: Optional[int] = None
    photo_url: Optional[str] = None
    is_available: bool
    sort_order: int
    is_bestseller: bool = False
    is_new: bool = False
    is_spicy: bool = False
    is_chef_choice: bool = False
    is_popular: bool = False
    # Phase 3: расписание доступности (admin response — показываем оба поля).
    available_from:  Optional[time] = None
    available_until: Optional[time] = None

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# КАТЕГОРИИ (admin response)
# ──────────────────────────────────────────

class CategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    products: List[ProductResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# MENU — ProductCreate/Update и Category CRUD
# ──────────────────────────────────────────

class ProductCreate(BaseModel):
    category_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=255)
    # S2-5: price nullable — None для продуктов с вариантами.
    # Если передана — должна быть >= 0. Отрицательная цена недопустима.
    # Примечание: продукт без price должен иметь варианты — иначе заказ невозможен.
    price: Optional[int] = Field(None, ge=0)
    description: Optional[str] = Field(None, max_length=1000)
    photo_url: Optional[str] = None
    is_available: bool = True
    sort_order: int = Field(0, ge=0)
    is_bestseller: bool = False
    is_new: bool = False
    is_spicy: bool = False
    is_chef_choice: bool = False
    is_popular: bool = False
    # Phase 3: расписание доступности. NULL/NULL = без расписания.
    available_from:  Optional[time] = None
    available_until: Optional[time] = None

    @field_validator("photo_url", mode="before")
    @classmethod
    def validate_photo_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_url(v)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[int] = Field(None, gt=0)
    description: Optional[str] = Field(None, max_length=1000)
    photo_url: Optional[str] = None
    is_available: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0)
    category_id: Optional[int] = Field(None, gt=0)
    is_bestseller: Optional[bool] = None
    is_new: Optional[bool] = None
    is_spicy: Optional[bool] = None
    is_chef_choice: Optional[bool] = None
    is_popular: Optional[bool] = None
    # Phase 3: расписание доступности. Передать None чтобы очистить поле.
    available_from:  Optional[time] = None
    available_until: Optional[time] = None

    @field_validator("photo_url", mode="before")
    @classmethod
    def validate_photo_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_url(v)


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    sort_order: int = Field(0, ge=0)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    sort_order: Optional[int] = Field(None, ge=0)


# ──────────────────────────────────────────
# PRODUCT VARIANT SCHEMAS  (S2-3)
# ──────────────────────────────────────────

class VariantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(..., ge=0)
    sort_order: int = Field(0, ge=0)
    is_active: bool = True
    # Phase 3: временная недоступность варианта ("Sold out").
    is_available: bool = True


class VariantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[int] = Field(None, ge=0)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
    # Phase 3: управление временной недоступностью варианта.
    is_available: Optional[bool] = None


class VariantResponse(BaseModel):
    id:           int
    product_id:   int
    name:         str
    price:        int
    sort_order:   int
    is_active:    bool
    # Phase 3: is_available для admin UI.
    is_available: bool
    created_at:   datetime
    updated_at:   datetime

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────
# MODIFIER GROUP SCHEMAS  (S2-4)
# ──────────────────────────────────────────

class ModifierGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    min_selections: int = Field(0, ge=0)
    max_selections: int = Field(1, ge=1)
    sort_order: int = Field(0, ge=0)
    is_active: bool = True

    @model_validator(mode="after")
    def check_min_lte_max(self) -> "ModifierGroupCreate":
        if self.min_selections > self.max_selections:
            raise ValueError("min_selections не может быть больше max_selections")
        return self


class ModifierGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    min_selections: Optional[int] = Field(None, ge=0)
    max_selections: Optional[int] = Field(None, ge=1)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class ModifierGroupResponse(BaseModel):
    id:             int
    product_id:     int
    name:           str
    min_selections: int
    max_selections: int
    sort_order:     int
    is_active:      bool
    created_at:     datetime
    updated_at:     datetime

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────
# MODIFIER OPTION SCHEMAS  (S2-4)
# ──────────────────────────────────────────

class ModifierOptionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price_adjustment: int = Field(0, ge=-1000000)
    sort_order: int = Field(0, ge=0)
    is_active: bool = True
    # Phase 3: временная недоступность опции ("Нет в наличии").
    is_available: bool = True


class ModifierOptionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price_adjustment: Optional[int] = Field(None, ge=-1000000)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
    # Phase 3: управление временной недоступностью опции.
    is_available: Optional[bool] = None


class ModifierOptionResponse(BaseModel):
    id:               int
    modifier_group_id: int
    name:             str
    price_adjustment: int
    sort_order:       int
    is_active:        bool
    # Phase 3: is_available для admin UI.
    is_available:     bool
    created_at:       datetime
    updated_at:       datetime

    model_config = ConfigDict(from_attributes=True)
