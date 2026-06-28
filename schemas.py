"""
schemas.py — Taomly Platform

Изменения v2:
  - OrderCreate: restaurant_id и client_telegram_id удалены (берутся из заголовков)
  - OrderCreate.items: min_length=1 — пустой заказ не проходит Pydantic
  - RestaurantCreate.slug: validator разрешает только [a-z0-9-]
  - primary_color / secondary_color / accent_color: validator проверяет HEX (#RRGGBB)
  - telegram_dispatcher_id: gt=0 — исключает 0 и отрицательные Telegram ID
"""

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ──────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ВАЛИДАТОРЫ
# ──────────────────────────────────────────
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_HEX_RE  = re.compile(r"^#[0-9A-Fa-f]{6}$")


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
    product_id: int
    # ge=1: Pydantic отклоняет quantity <= 0 до попадания в роутер
    quantity: int = Field(..., ge=1)


class OrderCreate(BaseModel):
    # restaurant_id убран — берётся из X-Restaurant-Id (верифицирован в get_telegram_user)
    # client_telegram_id убран — берётся из TelegramUser.id (верифицирован HMAC-SHA256)
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    order_type: Literal["delivery", "takeaway", "dine_in"]
    address: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    table_id: Optional[int] = None
    comment: Optional[str] = None
    # min_length=1: пустой список items не пройдёт до роутера
    items: List[OrderItemCreate] = Field(..., min_length=1)


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

    model_config = {"from_attributes": True}


# ЗАКАЗЫ — обновление статуса
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
    # restaurant_id убран — берётся из TelegramUser.restaurant_id
    client_name: str
    client_phone: str
    guests_count: int = Field(..., ge=1)
    reservation_time: datetime
    comment: Optional[str] = None


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
    # restaurant_id убран — берётся из TelegramUser.restaurant_id
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
    password: str


class AgencyRegister(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(..., min_length=8)


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
    name: str
    slug: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    admin_password: str = Field(..., min_length=6)

    # White Label Branding
    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#8B1A2E"
    secondary_color: Optional[str] = "#FAF6EE"
    accent_color: Optional[str] = "#D4A853"
    welcome_text: Optional[str] = None
    custom_domain: Optional[str] = None

    # Telegram
    telegram_bot_token: Optional[str] = None
    # gt=0: Telegram ID не может быть нулём или отрицательным
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return _validate_slug(v)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        return _validate_hex_color(v)


# ──────────────────────────────────────────
# RESTAURANT — обновление (Agency Owner)
# ──────────────────────────────────────────
class RestaurantUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None
    admin_password: Optional[str] = None

    # White Label Branding
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    welcome_text: Optional[str] = None
    custom_domain: Optional[str] = None

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        return _validate_hex_color(v)


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


# ──────────────────────────────────────────
# RESTAURANT ADMIN — авторизация
# ──────────────────────────────────────────
class RestaurantAdminLogin(BaseModel):
    slug: str
    password: str
