"""
schemas.py — Taomly Platform

Изменения v5 (S1-5: Location CRUD):
  - LocationCreate: поля для создания новой Location (slug, name, timezone, …).
  - LocationUpdate: все поля Optional — PATCH-семантика.
  - LocationResponse: полный отклик (id, restaurant_id, is_active, created_at, updated_at).
  - LocationListResponse: обёртка списка (locations + total).

Изменения v4 (Foundation Task 11 — Production Hardening):
  - RestaurantSettingsUpdate: delivery_fee и min_order_amount получили
    верхнюю границу le=10_000_000 (10 млн сум). Без верхней границы
    значение 9_999_999_999 вызывает PostgreSQL IntegerOverflow → HTTP 500
    вместо корректного HTTP 422. Граница достаточна для любого ресторана.
  - OrderCreate: location_lat ge=-90.0, le=90.0; location_lng ge=-180.0, le=180.0.
    NaN и Infinity от Pydantic float validator преобразуются в 422 через
    field_validator с явной проверкой math.isfinite().

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

import math
import re
from datetime import datetime, timezone
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ──────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ВАЛИДАТОРЫ
# ──────────────────────────────────────────
_SLUG_RE  = re.compile(r"^[a-z0-9-]+$")
_HEX_RE   = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-\(\)]{7,20}$")
_URL_RE   = re.compile(r"^https?://", re.IGNORECASE)

# Валидация custom_domain:
#   - каждая метка: [a-z0-9] по краям, внутри допустим одиночный дефис
#   - минимум две метки (домен второго уровня + TLD)
#   - TLD: только буквы, 2–63 символа
#   - IP-адреса (типа 127.0.0.1) не пропускаются — первая метка не может быть
#     чисто цифровой, если остальные тоже цифры
_DOMAIN_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
_DOMAIN_TLD_RE   = re.compile(r"^[a-zA-Z]{2,63}$")

# SSRF-защита: блокируем внутренние/приватные адреса
# Атакующий может передать http://169.254.169.254/ (AWS metadata),
# http://localhost:8000/api/superadmin/ или http://10.0.0.1/internal
_SSRF_BLOCK_RE = re.compile(
    r"^https?://"
    r"("
    r"localhost"
    r"|127\."
    r"|0\.0\.0\.0"
    r"|10\."
    r"|172\.(1[6-9]|2[0-9]|3[01])\."
    r"|192\.168\."
    r"|169\.254\."          # AWS/Azure link-local metadata
    r"|::1"
    r"|\[::1\]"
    r"|fc00:"
    r"|fd[0-9a-f]{2}:"
    r")",
    re.IGNORECASE,
)


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
    """
    Принимает только http:// или https:// URL с публичными хостами.

    Блокирует:
      - localhost / 127.x.x.x / ::1
      - Приватные диапазоны: 10.x, 172.16-31.x, 192.168.x
      - AWS/Azure link-local metadata: 169.254.x
      - IPv6 loopback и ULA-диапазоны

    Это защита от SSRF — атакующий не может передать URL внутренней сети.
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
    # Дополнительная проверка через urlparse — ловит http://user@localhost/
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


def _validate_custom_domain(value: Optional[str]) -> Optional[str]:
    """
    Принимает только корректные доменные имена вида restaurant.example.com или
    menu.example.uz. Отклоняет:
      - IP-адреса (127.0.0.1, 10.0.0.1 и т.д.)
      - localhost и его варианты
      - метки с двойным дефисом в произвольном месте (xn-- IDN разрешены явно)
      - метки, начинающиеся или заканчивающиеся дефисом
      - TLD короче 2 символов или содержащий цифры
      - однокомпонентные имена (нет точки)
      - пустую строку и строки с пробелами
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if " " in v or "\t" in v:
        raise ValueError("Доменное имя не может содержать пробелы")

    # Снимаем опциональный trailing dot (FQDN-стиль)
    if v.endswith("."):
        v = v[:-1]

    parts = v.split(".")
    if len(parts) < 2:
        raise ValueError(
            "Укажите полное доменное имя, например restaurant.example.com"
        )

    # TLD — последняя часть, только буквы
    tld = parts[-1]
    if not _DOMAIN_TLD_RE.match(tld):
        raise ValueError(
            f"Неверный TLD «{tld}». TLD должен содержать только буквы (2–63 символа)"
        )

    # Все части кроме TLD — проверяем по метке
    for label in parts[:-1]:
        if not label:
            raise ValueError("Доменное имя содержит пустую метку (двойная точка?)")
        if not _DOMAIN_LABEL_RE.match(label):
            raise ValueError(
                f"Неверная метка домена «{label}». "
                "Метка должна начинаться и заканчиваться на букву/цифру, "
                "внутри допустимы буквы, цифры и одиночный дефис"
            )

    # Блокируем IP-адреса: если все части — цифры → это IP, не домен
    if all(part.isdigit() for part in parts):
        raise ValueError(
            "IP-адрес не допускается в качестве custom_domain. "
            "Укажите доменное имя."
        )

    # Блокируем localhost явно (на случай "localhost.localdomain" и т.п.)
    if parts[0].lower() == "localhost" or v.lower() == "localhost":
        raise ValueError("localhost не допускается в качестве custom_domain")

    return v


# ──────────────────────────────────────────
# ПРОДУКТЫ
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
    # S2-5: variant_id — опциональный. Обязателен только если у продукта есть варианты.
    # Для legacy-продуктов без вариантов должен быть None/отсутствовать.
    variant_id: Optional[int] = Field(None, gt=0)


def _validate_coordinate(value: Optional[float], min_val: float, max_val: float, name: str) -> Optional[float]:
    """
    Проверяет координату на допустимый диапазон, NaN и Infinity.

    Pydantic float validator пропускает float("nan") и float("inf") без ошибки.
    Эти значения записываются в PostgreSQL DOUBLE PRECISION без исключения,
    но ломают любые арифметические операции над координатами в будущей аналитике.

    Foundation Task 11.3: явная проверка math.isfinite() блокирует NaN/Infinity
    до попадания в БД.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError(f"{name} не может быть NaN или бесконечностью")
    if not (min_val <= value <= max_val):
        raise ValueError(f"{name} должна быть в диапазоне [{min_val}, {max_val}]")
    return value


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

    @field_validator("location_lat", mode="after")
    @classmethod
    def validate_lat(cls, v: Optional[float]) -> Optional[float]:
        # Foundation Task 11.3: диапазон [-90, 90], блокируем NaN/Infinity
        return _validate_coordinate(v, -90.0, 90.0, "location_lat")

    @field_validator("location_lng", mode="after")
    @classmethod
    def validate_lng(cls, v: Optional[float]) -> Optional[float]:
        # Foundation Task 11.3: диапазон [-180, 180], блокируем NaN/Infinity
        return _validate_coordinate(v, -180.0, 180.0, "location_lng")

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
    # S2-5: variant_name — snapshot имени варианта. NULL для legacy-заказов.
    variant_name: Optional[str] = None
    price: int
    quantity: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: int
    restaurant_id: int
    # S1-3: location_id — canonical operational tenant scope.
    # Populated for all orders after migration 0012 backfill.
    location_id: int
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
    location_id: int  # S1-4

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
    location_id: int  # S1-4
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
_SUPPORTED_LANGUAGES = {"uz", "ru", "en"}


class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = None
    address: Optional[str] = Field(None, max_length=300)
    admin_password: str = Field(..., min_length=8, max_length=128)

    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#8B1A2E"
    secondary_color: Optional[str] = "#FAF6EE"
    accent_color: Optional[str] = "#D4A853"
    welcome_text: Optional[str] = Field(None, max_length=300)
    custom_domain: Optional[str] = None

    telegram_bot_token: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    # Язык клиентского UI: uz / ru / en. Default: uz.
    language: Optional[str] = Field("uz", pattern=r"^(uz|ru|en)$")

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

    @field_validator("custom_domain", mode="before")
    @classmethod
    def validate_custom_domain(cls, v: Optional[str]) -> Optional[str]:
        return _validate_custom_domain(v)

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
    # is_active намеренно отсутствует:
    # деактивация ресторана — через DELETE /api/agency/restaurants/{id}
    # Не принимать is_active через PATCH — защита от случайной деактивации.
    admin_password: Optional[str] = Field(None, min_length=8, max_length=128)

    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    welcome_text: Optional[str] = Field(None, max_length=300)
    custom_domain: Optional[str] = None

    telegram_bot_token: Optional[str] = None
    telegram_dispatcher_id: Optional[int] = Field(None, gt=0)

    # Язык клиентского UI: uz / ru / en.
    language: Optional[str] = Field(None, pattern=r"^(uz|ru|en)$")

    @field_validator("primary_color", "secondary_color", "accent_color", mode="before")
    @classmethod
    def validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        return _validate_hex_color(v)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_url(v)

    @field_validator("custom_domain", mode="before")
    @classmethod
    def validate_custom_domain(cls, v: Optional[str]) -> Optional[str]:
        return _validate_custom_domain(v)

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


# ──────────────────────────────────────────
# RESTAURANT PUBLIC API — response models
# ──────────────────────────────────────────

class RestaurantSettingsResponse(BaseModel):
    working_hours:    str = ""
    delivery_fee:     int = 0
    min_order_amount: int = 0
    timezone:         str = "Asia/Tashkent"
    # currency — валюта ресторана для отображения цен.
    # Дефолт "UZS" обеспечивает обратную совместимость.
    currency:         str = "UZS"
    # language — язык клиентского UI и Telegram-уведомлений.
    # Допустимые значения: uz, ru, en. Дефолт "uz".
    language:         str = "uz"

    model_config = ConfigDict(from_attributes=True)


class RestaurantSettingsUpdateResponse(RestaurantSettingsResponse):
    ok: bool = True


# S2-5: Минимальный public response для варианта.
# Только активные варианты попадают сюда (фильтрация в restaurants.py).
class VariantPublicResponse(BaseModel):
    id:         int
    name:       str
    price:      int
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class ProductPublicResponse(BaseModel):
    id:            int
    name:          str
    description:   Optional[str] = None
    # S2-5: nullable — None для variant-продуктов.
    price:         Optional[int] = None
    photo_url:     Optional[str] = None
    is_available:  bool
    sort_order:    int
    is_bestseller: bool
    is_new:        bool
    is_spicy:      bool
    is_chef_choice: bool
    is_popular:    bool
    # S2-5: активные варианты продукта. Пустой список для legacy-продуктов.
    variants:      List[VariantPublicResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CategoryPublicResponse(BaseModel):
    id:         int
    name:       str
    sort_order: int
    products:   List[ProductPublicResponse]

    model_config = ConfigDict(from_attributes=True)


class RestaurantPublicResponse(BaseModel):
    id:                     int
    name:                   str
    slug:                   str
    description:            Optional[str] = None
    phone:                  Optional[str] = None
    address:                Optional[str] = None
    is_waiter_call_enabled: bool
    # White Label branding
    logo_url:               Optional[str] = None
    primary_color:          str
    secondary_color:        str
    accent_color:           str
    welcome_text:           Optional[str] = None
    # Delivery settings
    working_hours:          str = ""
    delivery_fee:           int = 0
    min_order_amount:       int = 0
    # Валюта ресторана для форматирования цен на фронтенде.
    # Дефолт "UZS" обеспечивает обратную совместимость.
    currency:               str = "UZS"
    # Язык клиентского UI. Фронтенд загружает /i18n/{language}.json.
    # Дефолт "uz" обеспечивает обратную совместимость.
    language:               str = "uz"
    # Menu (only available products, grouped by category)
    categories:             List[CategoryPublicResponse]
    # telegram_bot_token_encrypted намеренно отсутствует — защита токена

    model_config = ConfigDict(from_attributes=True)


class TableResponse(BaseModel):
    restaurant_id:   int
    restaurant_name: str
    slug:            str
    table_id:        int
    table_number:    str

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────
# S1-5 — LOCATION CRUD schemas
# ──────────────────────────────────────────

# Допустимые значения совпадают с CHECK-constraint в migration 0010 / models.py.
_LOCATION_CURRENCIES = {"UZS", "KZT", "RUB", "USD", "TRY", "AED"}
_LOCATION_LANGUAGES  = {"uz", "ru", "en"}


class LocationCreate(BaseModel):
    """Схема создания новой Location.

    restaurant_id берётся из JWT-токена в роутере — клиент не передаёт его.
    slug обязателен, глобально уникален (ck на уровне БД), только [a-z0-9-].
    """
    name:                    str   = Field(..., min_length=1, max_length=255)
    slug:                    str   = Field(..., min_length=1, max_length=100)
    address:                 Optional[str]   = Field(None, max_length=500)
    phone:                   Optional[str]   = Field(None, max_length=50)
    timezone:                str   = Field("Asia/Tashkent", max_length=64)
    working_hours:           Optional[str]   = Field(None, max_length=100)
    delivery_fee:            int   = Field(0, ge=0, le=10_000_000)
    min_order_amount:        int   = Field(0, ge=0, le=10_000_000)
    currency:                str   = Field("UZS", max_length=10)
    language:                str   = Field("uz", max_length=5)
    is_waiter_call_enabled:  bool  = False

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError("slug может содержать только строчные буквы, цифры и дефис")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if v not in _LOCATION_CURRENCIES:
            raise ValueError(f"currency должна быть одним из: {sorted(_LOCATION_CURRENCIES)}")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in _LOCATION_LANGUAGES:
            raise ValueError(f"language должна быть одним из: {sorted(_LOCATION_LANGUAGES)}")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError("Некорректный формат телефона")
        return v


class LocationUpdate(BaseModel):
    """Схема обновления Location (PATCH-семантика: все поля Optional)."""
    name:                    Optional[str]   = Field(None, min_length=1, max_length=255)
    slug:                    Optional[str]   = Field(None, min_length=1, max_length=100)
    address:                 Optional[str]   = Field(None, max_length=500)
    phone:                   Optional[str]   = Field(None, max_length=50)
    timezone:                Optional[str]   = Field(None, max_length=64)
    working_hours:           Optional[str]   = Field(None, max_length=100)
    delivery_fee:            Optional[int]   = Field(None, ge=0, le=10_000_000)
    min_order_amount:        Optional[int]   = Field(None, ge=0, le=10_000_000)
    currency:                Optional[str]   = Field(None, max_length=10)
    language:                Optional[str]   = Field(None, max_length=5)
    is_waiter_call_enabled:  Optional[bool]  = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SLUG_RE.match(v):
            raise ValueError("slug может содержать только строчные буквы, цифры и дефис")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _LOCATION_CURRENCIES:
            raise ValueError(f"currency должна быть одним из: {sorted(_LOCATION_CURRENCIES)}")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _LOCATION_LANGUAGES:
            raise ValueError(f"language должна быть одним из: {sorted(_LOCATION_LANGUAGES)}")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError("Некорректный формат телефона")
        return v


class LocationResponse(BaseModel):
    """Полный отклик Location (используется в list и detail)."""
    id:                      int
    restaurant_id:           int
    name:                    str
    slug:                    str
    is_active:               bool
    address:                 Optional[str]
    phone:                   Optional[str]
    timezone:                str
    working_hours:           Optional[str]
    delivery_fee:            int
    min_order_amount:        int
    currency:                str
    language:                str
    is_waiter_call_enabled:  bool
    created_at:              datetime
    updated_at:              datetime

    model_config = ConfigDict(from_attributes=True)


class LocationListResponse(BaseModel):
    """Список Location ресторана + счётчик."""
    locations: List[LocationResponse]
    total:     int


# ──────────────────────────────────────────
# SUPERADMIN — response models
# ──────────────────────────────────────────

class SAAgencyItem(BaseModel):
    id:               int
    name:             str
    email:            str
    is_active:        bool
    created_at:       str
    restaurant_count: int

    model_config = ConfigDict(from_attributes=True)


class SAAgencyListResponse(BaseModel):
    total: int
    items: List[SAAgencyItem]


class SAAgencyDetailRestaurant(BaseModel):
    id:         int
    name:       str
    slug:       str
    is_active:  bool
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class SAAgencyDetailResponse(BaseModel):
    id:          int
    name:        str
    email:       str
    is_active:   bool
    created_at:  str
    restaurants: List[SAAgencyDetailRestaurant]


class SAAgencyCreateResponse(BaseModel):
    id:    int
    name:  str
    email: str


class SAAgencyUpdateResponse(BaseModel):
    ok:        bool
    id:        int
    is_active: bool


class SAImpersonateResponse(BaseModel):
    access_token: str
    agency_name:  str


class SARestaurantItem(BaseModel):
    id:         int
    name:       str
    slug:       str
    address:    Optional[str] = None
    is_active:  bool
    agency_id:  int
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class SARestaurantListResponse(BaseModel):
    total: int
    items: List[SARestaurantItem]


class SAFreezeResponse(BaseModel):
    ok:        bool
    is_active: bool


class SATransferResponse(BaseModel):
    ok:            bool
    restaurant_id: int
    new_agency_id: int


class SARecentAgencyItem(BaseModel):
    id:               int
    name:             str
    email:            str
    is_active:        bool
    created_at:       str
    restaurant_count: int


class SARecentRestaurantItem(BaseModel):
    id:         int
    name:       str
    slug:       str
    is_active:  bool
    agency_id:  int
    created_at: str


class SADashboardCounters(BaseModel):
    total:          int
    active:         int
    inactive:       int
    new_this_month: int


class SADashboardResponse(BaseModel):
    agencies:            SADashboardCounters
    restaurants:         SADashboardCounters
    mrr:                 int
    arr:                 int
    recent_agencies:     List[SARecentAgencyItem]
    recent_restaurants:  List[SARecentRestaurantItem]


# ──────────────────────────────────────────
# RESTAURANT TABLES — management schemas
# ──────────────────────────────────────────

_TABLE_NUMBER_RE = re.compile(r"^[A-Za-z0-9\u0400-\u04FF_\- ]+$")


class TableCreateRequest(BaseModel):
    table_number: str = Field(..., min_length=1, max_length=50, description="Номер или название стола (1, 2, VIP...)")

    @field_validator("table_number")
    @classmethod
    def validate_table_number(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Номер стола не может быть пустым")
        if not _TABLE_NUMBER_RE.match(v):
            raise ValueError(
                "Номер стола может содержать только буквы, цифры, "
                "дефис, подчёркивание и пробел"
            )
        return v


class TableItem(BaseModel):
    id:           int
    table_number: str
    created_at:   str

    model_config = ConfigDict(from_attributes=True)


class TablesListResponse(BaseModel):
    tables: List[TableItem]
    total:  int


class TableCreateResponse(BaseModel):
    ok:           bool
    id:           int
    table_number: str


# ──────────────────────────────────────────
# PRODUCT VARIANT SCHEMAS  (S2-3)
# ──────────────────────────────────────────

class VariantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(..., ge=0)
    sort_order: int = Field(0, ge=0)
    is_active: bool = True


class VariantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[int] = Field(None, ge=0)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class VariantResponse(BaseModel):
    id:         int
    product_id: int
    name:       str
    price:      int
    sort_order: int
    is_active:  bool
    created_at: datetime
    updated_at: datetime

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


class ModifierOptionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    price_adjustment: Optional[int] = Field(None, ge=-1000000)
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class ModifierOptionResponse(BaseModel):
    id:                  int
    modifier_group_id:   int
    name:                str
    price_adjustment:    int
    sort_order:          int
    is_active:           bool
    created_at:          datetime
    updated_at:          datetime

    model_config = ConfigDict(from_attributes=True)
