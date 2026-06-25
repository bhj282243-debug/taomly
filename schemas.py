from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime


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

    class Config:
        from_attributes = True


# ──────────────────────────────────────────
# КАТЕГОРИИ
# ──────────────────────────────────────────
class CategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    products: List[ProductResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ──────────────────────────────────────────
# ЗАКАЗЫ — создание
# ──────────────────────────────────────────
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)


class OrderCreate(BaseModel):
    restaurant_id: int
    client_telegram_id: Optional[int] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    order_type: Literal["delivery", "takeaway", "dine_in"]
    address: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    table_id: Optional[int] = None
    comment: Optional[str] = None
    items: List[OrderItemCreate]


# ЗАКАЗЫ — ответ
class OrderItemResponse(BaseModel):
    id: int
    name: str
    price: int
    quantity: int

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    status: str
    order_type: str
    total_amount: int
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    address: Optional[str] = None
    comment: Optional[str] = None
    items: List[OrderItemResponse] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


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
    restaurant_id: int
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

    class Config:
        from_attributes = True


class ReservationStatusUpdate(BaseModel):
    status: Literal["new", "confirmed", "completed", "cancelled"]


# ──────────────────────────────────────────
# ВЫЗОВ ОФИЦИАНТА
# ──────────────────────────────────────────
class WaiterCallCreate(BaseModel):
    restaurant_id: int
    table_id: int


class WaiterCallResponse(BaseModel):
    id: int
    status: str
    table_id: int
    created_at: datetime

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ──────────────────────────────────────────
# RESTAURANT — создание и обновление (Agency Owner)
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
    telegram_dispatcher_id: Optional[int] = None


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
    telegram_dispatcher_id: Optional[int] = None


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

    class Config:
        from_attributes = True


# ──────────────────────────────────────────
# RESTAURANT ADMIN — авторизация
# ──────────────────────────────────────────
class RestaurantAdminLogin(BaseModel):
    slug: str
    password: str
