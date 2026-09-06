"""
schemas/orders.py — Taomly Platform

Order creation and response schemas.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.common import _validate_coordinate, _validate_phone


# ──────────────────────────────────────────
# ЗАКАЗЫ — создание
# ──────────────────────────────────────────

class OrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=99)
    # S2-5: variant_id — опциональный. Обязателен только если у продукта есть варианты.
    # Для legacy-продуктов без вариантов должен быть None/отсутствовать.
    variant_id: Optional[int] = Field(None, gt=0)
    # S2-8: список id выбранных ModifierOption.
    # Пустой список → нет модификаторов (backward compatible).
    # Дубликаты нормализуются: уникальные id (set), порядок не гарантируется.
    # Валидация min/max selections и tenant-цепочка — в routers/orders.py.
    modifier_option_ids: List[int] = Field(default_factory=list)

    @field_validator("modifier_option_ids", mode="before")
    @classmethod
    def deduplicate_modifier_ids(cls, v: list) -> list:
        """
        Нормализация дубликатов: уникальные id без изменения типа.
        Семантика: дважды выбрать одну опцию = выбрать один раз.
        Порядок не сохраняется (set → list) — сортировка не требуется API.
        """
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


# ──────────────────────────────────────────
# ЗАКАЗЫ — ответ
# ──────────────────────────────────────────

class SelectedModifierResponse(BaseModel):
    """
    S2-8: Snapshot выбранного модификатора в ответе на заказ.
    Данные берутся из order_item_modifiers (не из modifier_options),
    поэтому остаются неизменными даже после удаления опции из меню.
    """
    id:                  int
    modifier_option_id:  Optional[int] = None  # NULL если опция удалена
    name:                str
    price_adjustment:    int

    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(BaseModel):
    id: int
    name: str
    # S2-5: variant_name — snapshot имени варианта. NULL для legacy-заказов.
    variant_name: Optional[str] = None
    price: int
    quantity: int
    # S2-8: snapshot выбранных модификаторов. Пустой список для заказов без модификаторов.
    selected_modifiers: List[SelectedModifierResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


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
