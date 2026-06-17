from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime


# ПРОДУКТЫ
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


# КАТЕГОРИИ
class CategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    products: List[ProductResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ЗАКАЗ — создание
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)


class OrderCreate(BaseModel):
    restaurant_id: int
    client_telegram_id: int
    client_name: str
    client_phone: str
    order_type: Literal["delivery", "takeaway", "dine_in"]
    address: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    table_id: Optional[int] = None
    comment: Optional[str] = None
    items: List[OrderItemCreate]


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
    client_name: str
    client_phone: str
    items: List[OrderItemResponse] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


# БРОНЬ — создание
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
    reservation_time: datetime

    class Config:
        from_attributes = True


# ВЫЗОВ ОФИЦИАНТА
class WaiterCallCreate(BaseModel):
    restaurant_id: int
    table_id: int


class WaiterCallResponse(BaseModel):
    id: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
