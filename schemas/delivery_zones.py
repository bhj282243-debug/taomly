"""
schemas/delivery_zones.py — Taomly Platform
Phase 14: DeliveryZone request/response schemas.

DeliveryZoneCreate / DeliveryZoneUpdate: admin-only mutation schemas.
DeliveryZoneResponse: full admin response (includes all fields).
DeliveryZonePublicResponse: customer-facing (name, fee, min_order, eta only).

fee and min_order are in tiyins (same unit as Order.total_amount, Payment.amount).
eta_minutes: optional, must be > 0 if provided.
"""

# ruff: noqa: I001
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DeliveryZoneCreate(BaseModel):
    name:        str           = Field(..., min_length=1, max_length=100)
    fee:         int           = Field(0, ge=0)
    min_order:   int           = Field(0, ge=0)
    eta_minutes: Optional[int] = Field(None, gt=0)
    sort_order:  int           = Field(0, ge=0)
    is_active:   bool          = True


class DeliveryZoneUpdate(BaseModel):
    name:        Optional[str] = Field(None, min_length=1, max_length=100)
    fee:         Optional[int] = Field(None, ge=0)
    min_order:   Optional[int] = Field(None, ge=0)
    eta_minutes: Optional[int] = Field(None, gt=0)
    sort_order:  Optional[int] = Field(None, ge=0)
    is_active:   Optional[bool] = None


class DeliveryZoneResponse(BaseModel):
    id:          int
    location_id: int
    name:        str
    fee:         int
    min_order:   int
    eta_minutes: Optional[int]
    is_active:   bool
    sort_order:  int
    created_at:  datetime
    updated_at:  datetime

    model_config = {"from_attributes": True}


class DeliveryZonePublicResponse(BaseModel):
    """Customer-facing: no admin fields (no created_at, updated_at, location_id)."""
    id:          int
    name:        str
    fee:         int
    min_order:   int
    eta_minutes: Optional[int]

    model_config = {"from_attributes": True}
