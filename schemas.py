"""
schemas.py — Taomly Platform
Pydantic-схемы для валидации входных данных и сериализации ответов API.

Изменения v6 (Security):
  - _validate_url: добавлена SSRF-защита (blocklist внутренних адресов)
  - is_popular добавлен в ProductResponse, ProductCreate, ProductUpdate
"""

import re
from datetime import datetime, timezone
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ──────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ВАЛИДАТОРЫ
# ──────────────────────────────────────────
_SLUG_RE  = re.compile(r"^[a-z0-9-]+$")
_HEX_RE   = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-\(\)]{7,20}$")
_URL_RE   = re.compile(r"^https?://", re.IGNORECASE)

# SSRF-защита: блокируем внутренние/приватные адреса
_SSRF_BLOCK_RE = re.compile(
    r"^https?://"
    r"("
    r"localhost"
    r"|127\."
    r"|0\.0\.0\.0"
    r"|10\."
    r"|172\.(1[6-9]|2[0-9]|3[01])\."
    r"|192\.168\."
    r"|169\.254\."
    r"|::1"
    r"|\[::1\]"
    r"|fc00:"
    r"|fd[0-9a-f]{2}:"
    r")",
    re.IGNORECASE,
)


def _validate_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    if not _PHONE_RE.match(v):
        raise ValueError(
            "Неверный формат номера телефона. "
            "Допустимые форматы: +998901234567, +7 (999) 123-45-67"
        )
    return v


def _validate_url(value: Optional[str]) -> Optional[str]:
    """
    Принимает только http:// или https:// URL с публичными хостами.

    Блокирует SSRF: localhost, 127.x, 10.x, 172.16-31.x,
    192.168.x, 169.254.x (AWS metadata), IPv6 loopback и ULA.
    """
    if not value:
        return None
    v = value.strip()
    if not _URL_RE.match(v):
        raise ValueError("URL должен начинаться с http:// или https://")
    if _SSRF_BLOCK_RE.match(v):
        raise ValueError(
            "URL указывает на внутренний/приватный адрес. "
            "Используйте публичный URL изображения."
        )
    try:
        parsed = urlparse(v)
        host = parsed.hostname or ""
        if _SSRF_BLOCK_RE.match(f"https://{host}"):
            raise ValueError(
                "URL указывает на внутренний/приватный адрес. "
                "Используйте публичный URL изображения."
            )
    except ValueError:
        raise
    except Exception:
        raise ValueError("Невалидный URL")
    return v


# ──────────────────────────────────────────
# ПРОДУКТЫ
# ──────────────────────────────────────────
class ProductResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price: int
    photo_url: Optional[str] = None
    is_available: bool
    sort_order: int
    is_bestseller: bool = False
    is_new: bool = False
    is_spicy: bool = False
    is_chef_choice: bool = False
    is_popular: bool = False

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    category_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(..., gt=0)
    description: Optional[str] = Field(None, max_length=1000)
    photo_url: Optional[str] = None
    is_available: bool = True
    sort_order: int = Field(0, ge=0)
    is_bestseller: bool = False
    is_new: bool = False
    is_spicy: bool = False
    is_chef_choice: bool = False
    is_popular: bool = False

    @field_validator("photo_url", mode="before")
    @classmethod
    def validate_photo_url(cls, v):
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

    @field_validator("photo_url", mode="before")
    @classmethod
    def validate_photo_url(cls, v):
        return _validate_url(v)


# ──────────────────────────────────────────
# КАТЕГОРИИ
# ──────────────────────────────────────────
class CategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    products: List[ProductResponse] = []

    model_config = {"from_attributes": True}


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    sort_order: int = Field(0, ge=0)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    sort_order: Optional[int] = Field(None, ge=0)


# ──────────────────────────────────────────
# ЗАКАЗЫ
# ──────────────────────────────────────────
class OrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=99)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(..., min_length=1)
    order_type: Literal["dine_in", "delivery", "takeaway"] = "dine_in"
    table_id: Optional[int] = Field(None, gt=0)
    delivery_address: Optional[str] = Field(None, max_length=500)
    comment: Optional[str] = Field(None, max_length=500)
    customer_name: Optional[str] = Field(None, max_length=100)
    customer_phone: Optional[str] = None

    @field_validator("customer_phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return _validate_phone(v)

    @model_validator(mode="after")
    def validate_order_type_fields(self):
        if self.order_type == "dine_in" and not self.table_id:
            raise ValueError("table_id обязателен для dine_in заказа")
        if self.order_type == "delivery" and not self.delivery_address:
            raise ValueError("delivery_address обязателен для delivery заказа")
        return self


class OrderItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: int
    subtotal: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    status: str
    order_type: str
    total_amount: int
    comment: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[str] = None
    table_number: Optional[int] = None
    created_at: datetime
    items: List[OrderItemResponse] = []

    model_config = {"from_attributes": True}


class OrderStatusUpdate(BaseModel):
    status: Literal["pending", "confirmed", "preparing", "ready", "delivered", "cancelled"]


# ──────────────────────────────────────────
# РЕСТОРАНЫ
# ──────────────────────────────────────────
class RestaurantAdminLogin(BaseModel):
    slug: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=128)


class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    address: Optional[str] = Field(None, max_length=300)
    phone: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = Field(None, max_length=1000)

    @field_validator("slug", mode="before")
    @classmethod
    def validate_slug(cls, v):
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError("slug может содержать только строчные буквы, цифры и дефис")
        return v

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return _validate_phone(v)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v):
        return _validate_url(v)


class RestaurantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    address: Optional[str] = Field(None, max_length=300)
    phone: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = Field(None, max_length=1000)
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    bot_token: Optional[str] = Field(None, max_length=100)
    telegram_chat_id: Optional[str] = Field(None, max_length=50)
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return _validate_phone(v)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v):
        return _validate_url(v)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_color(cls, v):
        if not v:
            return v
        if not _HEX_RE.match(v):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return v


class RestaurantAdminResponse(BaseModel):
    id: int
    name: str
    slug: str
    address: Optional[str] = None
    phone: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RestaurantCreateResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    qr_url: str

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# АГЕНТСТВА
# ──────────────────────────────────────────
class AgencyLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class AgencyResponse(BaseModel):
    id: int
    name: str
    owner_email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# ТОКЕНЫ
# ──────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ──────────────────────────────────────────
# БРЕНДИНГ
# ──────────────────────────────────────────
class BrandingUpdate(BaseModel):
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    logo_url: Optional[str] = None
    welcome_title: Optional[str] = Field(None, max_length=100)
    welcome_subtitle: Optional[str] = Field(None, max_length=200)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_color(cls, v):
        if not v:
            return v
        if not _HEX_RE.match(v):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return v

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v):
        return _validate_url(v)


# ──────────────────────────────────────────
# СТОЛЫ
# ──────────────────────────────────────────
class TableCreate(BaseModel):
    number: int = Field(..., ge=1, le=999)
    label: Optional[str] = Field(None, max_length=50)


class TableResponse(BaseModel):
    id: int
    number: int
    label: Optional[str] = None
    qr_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# ПОДПИСКИ
# ──────────────────────────────────────────
class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    price_per_month: int
    orders_per_month: int
    max_products: int
    features: Optional[str] = None

    model_config = {"from_attributes": True}


class SubscriptionResponse(BaseModel):
    id: int
    plan: SubscriptionPlanResponse
    started_at: datetime
    expires_at: Optional[datetime] = None
    is_active: bool

    model_config = {"from_attributes": True}
