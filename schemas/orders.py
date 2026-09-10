"""
schemas/orders.py — Taomly Platform
Phase 7: Added currency field to OrderResponse.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.common import _validate_coordinate, _validate_phone


class OrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=99)
    variant_id: Optional[int] = Field(None, gt=0)
    modifier_option_ids: List[int] = Field(default_factory=list)

    @field_validator("modifier_option_ids", mode="before")
    @classmethod
    def deduplicate_modifier_ids(cls, v: list) -> list:
        if not v:
            return []
        seen = []
        seen_set = set()
        for item in v:
            if item not in seen_set:
                seen_set.add(item)
                seen.append(item)
        return seen


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
        return _validate_coordinate(v, -90.0, 90.0, "location_lat")

    @field_validator("location_lng", mode="after")
    @classmethod
    def validate_lng(cls, v: Optional[float]) -> Optional[float]:
        return _validate_coordinate(v, -180.0, 180.0, "location_lng")

    @model_validator(mode="after")
    def validate_order_type_fields(self) -> "OrderCreate":
        if self.order_type == "delivery" and not self.address:
            raise ValueError("Для заказа с доставкой укажите адрес (address)")
        if self.order_type == "dine_in" and not self.table_id:
            raise ValueError("Для заказа в зале укажите номер стола (table_id)")
        return self


class SelectedModifierResponse(BaseModel):
    id:                  int
    modifier_option_id:  Optional[int] = None
    name:                str
    price_adjustment:    int

    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(BaseModel):
    id: int
    name: str
    variant_name: Optional[str] = None
    price: int
    quantity: int
    selected_modifiers: List[SelectedModifierResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: int
    restaurant_id: int
    location_id: int
    status: str
    order_type: str
    total_amount: int
    # Phase 7: immutable currency snapshot from Cart.currency at checkout.
    # Backfilled for historical orders from location.currency (migration 0020).
    currency: str
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
        "new", "accepted", "preparing", "ready_for_delivery",
        "delivering", "completed", "cancelled",
    ]
