"""
modules/cart/schemas.py — Taomly Platform
Phase 6: Cart Engine Pydantic schemas.
Phase 7: Added CheckoutRequest.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


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

    Значения, которые НИКОГДА не принимаются от клиента (вычисляются server-side):
      total_amount, currency, unit_price, restaurant_id
    """
    order_type:      Literal["delivery", "takeaway", "dine_in"]
    client_name:     Optional[str] = Field(None, max_length=100)
    client_phone:    Optional[str] = Field(None, max_length=50)
    address:         Optional[str] = Field(None, max_length=300)
    table_id:        Optional[int] = Field(None, gt=0)
    comment:         Optional[str] = Field(None, max_length=500)
    idempotency_key: Optional[str] = Field(None, max_length=64)


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
