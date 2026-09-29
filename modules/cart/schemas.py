"""
modules/cart/schemas.py — Taomly Platform
Phase 6: Cart Engine Pydantic schemas.
Phase 7: Added CheckoutRequest.
Phase 14: Added zone_id, scheduled_at, location_lat, location_lng to CheckoutRequest.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── HELPERS ────────────────────────────────────────────────────────

def _validate_coordinate(v: Optional[float], min_val: float, max_val: float, name: str) -> Optional[float]:
    if v is None:
        return v
    if not (min_val <= v <= max_val):
        raise ValueError(f"{name} must be between {min_val} and {max_val}")
    return v


# ── REQUEST SCHEMAS ────────────────────────────────────────────────

class AddItemRequest(BaseModel):
    product_id:          int           = Field(..., gt=0)
    variant_id:          Optional[int] = Field(None, gt=0)
    modifier_option_ids: List[int]     = Field(default_factory=list)
    notes:               Optional[str] = Field(None, max_length=300)
    quantity:            int           = Field(1, ge=1, le=99)


class UpdateQuantityRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=99)


class CheckoutRequest(BaseModel):
    """
    POST /api/cart/checkout

    Values NEVER accepted from client (computed server-side):
      total_amount, subtotal, delivery_fee, currency, unit_price, restaurant_id

    Phase 14 additions:
      zone_id:      hint for delivery zone (server re-validates ownership)
      scheduled_at: target fulfillment time (server validates bounds)
      location_lat/lng: optional customer coordinates (informational)
    """
    order_type:      Literal["delivery", "takeaway", "dine_in"]
    client_name:     Optional[str]      = Field(None, max_length=100)
    client_phone:    Optional[str]      = Field(None, max_length=50)
    address:         Optional[str]      = Field(None, max_length=300)
    table_id:        Optional[int]      = Field(None, gt=0)
    comment:         Optional[str]      = Field(None, max_length=500)
    idempotency_key: Optional[str]      = Field(None, max_length=64)
    # Phase 14:
    zone_id:         Optional[int]      = Field(None, gt=0)
    scheduled_at:    Optional[datetime] = None
    location_lat:    Optional[float]    = None
    location_lng:    Optional[float]    = None

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def _validate_scheduled_tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("scheduled_at must be timezone-aware (include UTC offset).")
        return v

    @field_validator("location_lat", mode="after")
    @classmethod
    def _validate_lat(cls, v: Optional[float]) -> Optional[float]:
        return _validate_coordinate(v, -90.0, 90.0, "location_lat")

    @field_validator("location_lng", mode="after")
    @classmethod
    def _validate_lng(cls, v: Optional[float]) -> Optional[float]:
        return _validate_coordinate(v, -180.0, 180.0, "location_lng")


# ── RESPONSE SCHEMAS ───────────────────────────────────────────────

class CartItemModifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    modifier_option_id: Optional[int]
    name:               str
    price_adjustment:   int


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           int
    product_id:   int
    product_name: str
    variant_id:   Optional[int]
    variant_name: Optional[str]
    quantity:     int
    unit_price:   int
    modifiers:    List[CartItemModifierResponse] = []
    notes:        Optional[str]
    line_total:   int


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cart_id:       int
    restaurant_id: int
    currency:      str
    status:        str
    items:         List[CartItemResponse]
    subtotal:      int
    item_count:    int
