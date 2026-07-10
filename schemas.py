"""
schemas.py — Taomly Platform

Изменения v3:
  - OrderResponse: добавлен updated_at (M-3)
  - OrderCreate: валидация address обязателен при order_type=delivery (M-10)
  - OrderCreate: client_phone — regex валидация формата (M-12)
  - ReservationCreate: reservation_time должна быть в будущем (M-9)
  - ReservationCreate: client_phone — regex валидация
  - AgencyRegister: max_length=100 на name и password (L-3)
  - RestaurantCreate.name: max_length=100 (L-4)
  - ProductCreate/ProductUpdate: photo_url — HttpUrl-style валидация (L-8)
  - RestaurantCreate/Update: logo_url — URL валидация (L-8)
  - ProductResponse: добавлены badge поля (is_bestseller, is_new, is_spicy, is_chef_choice) (M-2)
  - ProductCreate/Update: добавлены badge поля (M-2)
  - Analytics и Billing схемы перенесены из routers/ (Sprint 3.2)
"""

import re
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ──────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ВАЛИДАТОРЫ
# ──────────────────────────────────────────
_SLUG_RE  = re.compile(r"^[a-z0-9-]+$")
_HEX_RE   = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-\(\)]{7,20}$")
_URL_RE   = re.compile(r"^https?://", re.IGNORECASE)


def _validate_slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            "slug может содержать только строчные латинские буквы, цифры и дефис"
        )
    return value


def _validate_hex_color(value: Optional[str]) -> Optional[str]:
    if value is not None and not _HEX_RE.match(value):
        raise ValueError("Цвет должен быть в формате #RRGGBB, например #8B1A2E")
    return value


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
    """Принимает только http:// или https:// URL."""
    if not value:
        return None
    v = value.strip()
    if not _URL_RE.match(v):
        raise ValueError("URL должен начинаться с http:// или https://")
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

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# КАТЕГОРИИ
# ──────────────────────────────────────────
class CategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    products: List[ProductResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────
# ЗАКАЗЫ — создание
# ──────────────────────────────────────────
class OrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=99)


class OrderCreate(BaseModel):
    client_name: Optional[str] = Field(None, max_length=100)
    client_phone: Optional[str] = None
    order_type: Literal["delivery", "takeaway", "dine_in"]
    address: Optional[str] = Field(None, max_length=300)
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    table_id: Optional[int] = Field(None, gt=0)
    comment: Optional[str] = Field(None, max_length=500)
    items: List[OrderItemCreate] = Field(..., min_length=1, max_length=50)

    @field_validator("client_phone", mode="before")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone(v)

    @model_validator(mode="after")
    def validate_order_type_fields(self) -> "OrderCreate":
        if self.order_type == "delivery" and not self.address:
            raise ValueError("Для заказа с доставкой укажите адрес (address)")
        if self.order_type == "dine_in" and not self.table_id:
            raise ValueError("Для заказа в зале укажите номер стола (table_id)")
        return self


# ЗАКАЗЫ — ответ
class OrderItemResponse(BaseModel):
    id: int
    name: str
    price: int
    quantity: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    restaurant_id: int
    status: str
    order_type: str
    total_amount: int
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    address: Optional[str] = None
    table_id: Optional[int] = None
    comment: Optional[str] = None
    items: List[OrderItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderStatusUpdate(BaseModel):
    status: Literal[
        "new",
        "accepted",
        "preparing",
        "ready_for_delivery",
        "delivering",
        "completed",
        "cancelled",
    ]


# ──────────────────────────────────────────
# БРОНЬ
# ──────────────────────────────────────────
class ReservationCreate(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=100)
    client_phone: str
    guests_count: int = Field(..., ge=1, le=100)
    reservation_time: datetime
    comment: Optional[str] = Field(None, max_length=500)

    @field_validator("client_phone", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = _validate_phone(v)
        if not result:
            raise ValueError("Номер телефона обязателен для брони")
        return result

    @field_validator("reservation_time", mode="after")
    @classmethod
    def validate_future_date(cls, v: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            from datetime import timezone as _tz
            v = v.replace(tzinfo=_tz.utc)
        if v <= now:
            raise ValueError("Время брони должно быть в будущем")
        return v


class ReservationResponse(BaseModel):
    id: int
    status: str
    client_name: str
    client_phone: str
    guests_count: int
    reservation_time: datetime
    comment: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReservationStatusUpdate(BaseModel):
    status: Literal["new", "confirmed", "completed", "cancelled"]


# ──────────────────────────────────────────
# ВЫЗОВ ОФИЦИАНТА
# ──────────────────────────────────────────
class WaiterCallCreate(BaseModel):
    table_id: int = Field(..., gt=0)


class WaiterCallResponse(BaseModel):
    id: int
    status: str
    table_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class WaiterCallStatusUpdate(BaseModel):
    status: Literal["active", "accepted", "completed", "cancelled"]


# ──────────────────────────────────────────
# AGENCY — авторизация
# ──────────────────────────────────────────
class AgencyLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class AgencyRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class AgencyResponse(BaseModel):
    id: int
    name: str
    owner_email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ──────────────────────────────────────────
# RESTAURANT — создание (Agency Owner)
# ──────────────────────────────────────────
class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = None
    address: Optional[str] = Field(None, max_length=300)
    admin_password: str = Field(..., min_length=6, max_length=128)

    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#8B1A2E"
    secondary_color: Optional[str] = "#FAF6EE"
    accent_color: Optional[str] = "#D4A853"
    welcome_text: Optional[str] = Field(None, max_length=300)
    custom_domain: Optional[str] = None

    telegram_bot_token: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return _validate_slug(v)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        return _validate_hex_color(v)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_url(v)

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone(v)


# ──────────────────────────────────────────
# RESTAURANT — обновление (Agency Owner)
# ──────────────────────────────────────────
class RestaurantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = None
    address: Optional[str] = Field(None, max_length=300)
    is_active: Optional[bool] = None
    admin_password: Optional[str] = Field(None, min_length=6, max_length=128)

    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    welcome_text: Optional[str] = Field(None, max_length=300)
    custom_domain: Optional[str] = None

    telegram_bot_token: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        return _validate_hex_color(v)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_url(v)

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone(v)


class RestaurantAdminResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: bool
    logo_url: Optional[str] = None
    primary_color: str
    secondary_color: str
    accent_color: str
    welcome_text: Optional[str] = None
    custom_domain: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RestaurantCreateResponse(RestaurantAdminResponse):
    webhook_status: str = "skipped"
    webhook_detail: Optional[str] = None


# ──────────────────────────────────────────
# RESTAURANT ADMIN — авторизация
# ──────────────────────────────────────────
class RestaurantAdminLogin(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=1, max_length=128)


# ──────────────────────────────────────────
# MENU — ProductCreate/Update и Category
# ──────────────────────────────────────────
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
# ANALYTICS SCHEMAS
# ──────────────────────────────────────────

class SummaryResponse(BaseModel):
    period: str
    revenue: int
    orders_total: int
    orders_completed: int
    orders_cancelled: int
    avg_check: int
    returning_clients: int
    new_clients: int


class DayRevenueItem(BaseModel):
    date: str
    revenue: int
    orders: int


class DishItem(BaseModel):
    rank: int
    name: str
    qty: int
    revenue: int


class HourItem(BaseModel):
    hour: int
    orders: int


class OrderTypeItem(BaseModel):
    order_type: str
    orders: int
    revenue: int


# ──────────────────────────────────────────
# BILLING SCHEMAS
# ──────────────────────────────────────────

class PlanResponse(BaseModel):
    id: int
    name: str
    price: int
    currency: str
    orders_per_month: int
    products_limit: int
    users_limit: int
    description: Optional[str]


class SubscriptionResponse(BaseModel):
    plan_id: int
    plan_name: str
    price: int
    currency: str
    orders_per_month: int
    products_limit: int
    started_at: str
    expires_at: Optional[str]
    is_active: bool


class UsageResponse(BaseModel):
    period: str
    orders_used: int
    orders_limit: int
    orders_remaining: int
    orders_pct: int
    products_used: int
    products_limit: int
    products_remaining: int
    products_pct: int


class SubscribeResponse(BaseModel):
    success: bool
    plan_id: int
    plan_name: str
    message: str
