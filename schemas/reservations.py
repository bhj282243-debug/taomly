"""
schemas/reservations.py — Taomly Platform

Reservation and waiter call schemas.
WaiterCall schemas are placed here as they follow the same pattern
(guest in-restaurant operations: Create/Response/StatusUpdate).
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from schemas.common import _validate_phone


# ──────────────────────────────────────────
# БРОНЬ
# ──────────────────────────────────────────

class ReservationCreate(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=100)
    client_phone: str
    guests_count: int = Field(..., ge=1, le=100)
    reservation_time: datetime
    comment: Optional[str] = Field(None, max_length=500)

    @field_validator("client_phone", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = _validate_phone(v)
        if not result:
            raise ValueError("Номер телефона обязателен для брони")
        return result

    @field_validator("reservation_time", mode="after")
    @classmethod
    def validate_future_date(cls, v: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            from datetime import timezone as _tz
            v = v.replace(tzinfo=_tz.utc)
        if v <= now:
            raise ValueError("Время брони должно быть в будущем")
        return v


class ReservationResponse(BaseModel):
    id: int
    status: str
    client_name: str
    client_phone: str
    guests_count: int
    reservation_time: datetime
    comment: Optional[str] = None
    created_at: datetime
    location_id: int  # S1-4

    model_config = {"from_attributes": True}


class ReservationStatusUpdate(BaseModel):
    status: Literal["new", "confirmed", "completed", "cancelled"]


# ──────────────────────────────────────────
# ВЫЗОВ ОФИЦИАНТА
# ──────────────────────────────────────────

class WaiterCallCreate(BaseModel):
    table_id: int = Field(..., gt=0)


class WaiterCallResponse(BaseModel):
    id: int
    status: str
    table_id: int
    location_id: int  # S1-4
    created_at: datetime

    model_config = {"from_attributes": True}


class WaiterCallStatusUpdate(BaseModel):
    status: Literal["active", "accepted", "completed", "cancelled"]
