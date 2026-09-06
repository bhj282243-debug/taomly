"""
schemas/auth.py — Taomly Platform

Agency authentication, restaurant admin login, restaurant CRUD schemas.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from schemas.common import (
    _validate_custom_domain,
    _validate_hex_color,
    _validate_phone,
    _validate_slug,
    _validate_url,
)

_SUPPORTED_LANGUAGES = {"uz", "ru", "en"}


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
