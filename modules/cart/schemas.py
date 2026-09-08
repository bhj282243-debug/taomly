"""
modules/cart/schemas.py — Taomly Platform
Phase 6: Cart Engine Pydantic schemas.

Client-supplied prices are never accepted.
restaurant_id is never accepted from request body.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────
# REQUEST SCHEMAS
# ──────────────────────────────────────────

class AddItemRequest(BaseModel):
    """
    POST /api/cart/items

    product_id and variant_id identify the menu item.
    modifier_option_ids: validated server-side; duplicate IDs → HTTP 400.
    notes: optional per-item customer note.
    quantity: defaults to 1.

    Prices are NEVER accepted from client — computed server-side from DB.
    """
    product_id:          int           = Field(..., gt=0)
    variant_id:          Optional[int] = Field(None, gt=0)
    modifier_option_ids: List[int]     = Field(default_factory=list)
    notes:               Optional[str] = Field(None, max_length=300)
    quantity:            int           = Field(1, ge=1, le=99)


class UpdateQuantityRequest(BaseModel):
    """PATCH /api/cart/items/{item_id}"""
    quantity: int = Field(..., ge=1, le=99)


# ──────────────────────────────────────────
# RESPONSE SCHEMAS
# ──────────────────────────────────────────

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
