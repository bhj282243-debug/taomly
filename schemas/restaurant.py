"""
schemas/restaurant.py — Taomly Platform

Restaurant public API, settings, tables, and location schemas.

Includes CategoryPublicResponse and ProductPublicResponse here (not in menu_public)
because both routers/restaurants.py and routers/menu_public.py import them,
and placing them in menu_public would create wrong coupling direction.
"""

import re
from datetime import datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.common import _PHONE_RE, _SLUG_RE, _validate_phone, _validate_slug


# ──────────────────────────────────────────
# RESTAURANT PUBLIC API — settings
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


# ──────────────────────────────────────────
# PUBLIC MENU REPRESENTATIONS
# (shared between routers/restaurants.py and routers/menu_public.py)
# ──────────────────────────────────────────

# S2-5: Минимальный public response для варианта.
# Только активные варианты попадают сюда (фильтрация в restaurants.py).
class VariantPublicResponse(BaseModel):
    id:           int
    name:         str
    price:        int
    sort_order:   int
    # Phase 3: is_available передаётся клиенту для отображения "Sold out".
    # Варианты с is_active=false скрыты полностью (не включаются в response).
    # Варианты с is_active=true, is_available=false → в response, но disabled.
    is_available: bool

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────
# S2-8: PUBLIC MODIFIER SCHEMAS
# ──────────────────────────────────────────

class ModifierOptionPublicResponse(BaseModel):
    """Публичная схема опции модификатора. Только активные опции."""
    id:               int
    name:             str
    price_adjustment: int
    sort_order:       int
    # Phase 3: is_available передаётся клиенту для disabled state.
    # Опции с is_active=false скрыты полностью.
    # Опции с is_active=true, is_available=false → в response, но disabled.
    is_available:     bool

    model_config = ConfigDict(from_attributes=True)


class ModifierGroupPublicResponse(BaseModel):
    """Публичная схема группы модификаторов. Только активные группы с активными опциями."""
    id:             int
    name:           str
    min_selections: int
    max_selections: int
    sort_order:     int
    options:        List[ModifierOptionPublicResponse] = Field(default_factory=list)

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
    # Phase 3: расписание (передаётся для информации фронтенду, не для клиентской фильтрации).
    available_from:  Optional[time] = None
    available_until: Optional[time] = None
    # S2-5: активные варианты продукта. Пустой список для legacy-продуктов.
    # Phase 3: варианты с is_active=true, is_available=false включаются (disabled state).
    variants:      List[VariantPublicResponse] = Field(default_factory=list)
    # S2-8: активные группы модификаторов с активными опциями.
    modifier_groups: List[ModifierGroupPublicResponse] = Field(default_factory=list)

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


# ──────────────────────────────────────────
# RESTAURANT TABLES — management schemas
# ──────────────────────────────────────────

_TABLE_NUMBER_RE = re.compile(r"^[A-Za-z0-9\u0400-\u04FF_\- ]+$")


class TableCreateRequest(BaseModel):
    table_number: str = Field(
        ..., min_length=1, max_length=50, description="Номер или название стола (1, 2, VIP...)"
    )

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
